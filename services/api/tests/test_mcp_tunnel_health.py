"""Whether the tunnel works, told apart from whether it was started.

Every check here was written against a machine where the tunnel reported
itself running and served another application's web page to anyone who dialled
it. Nothing was broken in a way any surface could show: the port was taken by a
program that answered HTTP 200, `tunnel-client doctor` calls that reachable,
and the status file was written "running" the moment the client process
existed. The one honest record was a line in a log nobody reads.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from trendrelay_api.integrations.mcp import service, tunnel


@pytest.fixture
def status_file(monkeypatch, tmp_path):
    """The status files, in a directory of this test's own."""
    monkeypatch.setattr(service, "MCP_DIR", tmp_path)
    monkeypatch.setattr(service, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(tunnel, "STATUS_FILE", tmp_path / "tunnel-status.json")
    monkeypatch.delenv(service.PORT_ENV, raising=False)
    return tmp_path


def _serve(body: bytes, content_type: str) -> tuple[int, HTTPServer]:
    """A one-route HTTP server on a free port, standing in for a squatter."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's spelling
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """Quiet: the test's output is the test's own."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port, server


def test_a_port_another_program_holds_is_not_the_port_we_serve_on(monkeypatch) -> None:
    """The failure itself: pinned to a taken port, the server never came up."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        taken = squatter.getsockname()[1]
        monkeypatch.setattr(service, "preferred_port", lambda: taken)

        chosen = service.choose_port()

        assert chosen != taken
        assert service.port_is_free(chosen)


def test_a_free_preferred_port_is_the_one_used(monkeypatch) -> None:
    """Moving ports is the exception, not the habit: 8765 stays 8765."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    monkeypatch.setattr(service, "preferred_port", lambda: free)

    assert service.choose_port() == free


def test_the_running_server_names_the_port_it_actually_took(status_file) -> None:
    """Whoever hands the URL to the tunnel has to be told, not left to guess."""
    service.write_status("running", "Serving.", port=52773)

    assert service.port() == 52773
    assert service.server_url() == "http://127.0.0.1:52773/mcp"


def test_a_stopped_server_does_not_keep_advertising_its_old_port(status_file) -> None:
    service.write_status("running", "Serving.", port=52773)
    service.write_status("stopped", "The MCP server is stopped.")

    assert service.port() == service.preferred_port()


def test_something_answering_is_not_this_app_answering(status_file, monkeypatch) -> None:
    """The check the doctor cannot make.

    `tunnel-client doctor` reports `mcp_server_reachable PASS` on any HTTP 200,
    and the 200 in question was another application's home page.
    """
    port, server = _serve(b"<!doctype html><title>Another app</title>", "text/html")
    try:
        service.write_status("running", "Serving.", port=port)

        outcome = tunnel.local_server_check()

        assert outcome["ok"] is False
        assert "another application" in outcome["detail"]
        assert str(port) in outcome["detail"]
    finally:
        server.shutdown()


def test_this_app_answering_is_recognised(status_file) -> None:
    described = json.dumps({
        "resource": "http://127.0.0.1/mcp", "resource_name": "TrendRelay workspace",
    }).encode()
    port, server = _serve(described, "application/json")
    try:
        service.write_status("running", "Serving.", port=port)

        outcome = tunnel.local_server_check()

        assert outcome["ok"] is True
        assert str(port) in outcome["detail"]
    finally:
        server.shutdown()


def test_nothing_answering_says_so_rather_than_guessing(status_file) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    service.write_status("running", "Serving.", port=closed)

    outcome = tunnel.local_server_check()

    assert outcome["ok"] is False
    assert "Nothing is answering" in outcome["detail"]


def test_a_skipped_optional_check_is_not_a_failed_one(monkeypatch) -> None:
    """The button said "problem" over a configuration the client passed.

    tunnel-client returns SKIP for optional things it found no reason to run -
    an uninstalled Codex plugin - while exiting 0 with `result: ok`.
    """
    import subprocess

    passing = json.dumps({
        "result": "ok",
        "checks": [
            {"id": "tunnel_id", "status": "PASS"},
            {"id": "codex_plugin", "status": "SKIP"},
        ],
    })
    monkeypatch.setattr(
        tunnel, "resolve_config",
        lambda: ({"tunnel_id": "t", "api_key": "k", "binary": "x", "log_level": "warn"}, None),
    )
    monkeypatch.setattr(tunnel, "child_env", lambda _config: {})
    monkeypatch.setattr(
        subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, passing, ""),
    )

    outcome = tunnel.run_doctor()

    assert outcome["ok"] is True


def test_the_status_carries_the_port_the_client_answers_on(status_file) -> None:
    """Nothing else can guess it: the supervisor picks a free one per attempt."""
    tunnel.write_status("running", "Forwarding.", health_port=62355)

    assert tunnel.status()["health_port"] == 62355


def test_a_tunnel_that_is_not_running_skips_the_connector_check(status_file) -> None:
    """Skipped, not failed. It has not been asked to run yet."""
    tunnel.write_status("stopped", "Not started.")

    outcome = tunnel.connector_check()

    assert outcome["skipped"] is True
