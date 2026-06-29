"""Opt-in Docker integration test for the container runtime."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

_ENABLE_ENV_NAME = "OMNIFETCH_RUN_DOCKER_TESTS"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_NAME = "omnifetch-docker-integration"
_HEALTH_TIMEOUT_S = 90.0
_REQUEST_TIMEOUT_S = 3.0
_POLL_INTERVAL_S = 0.5
_HTTP_BAD_REQUEST = 400


def _docker_tests_enabled(environ: Mapping[str, str]) -> bool:
    """Return whether Docker integration tests should run."""
    return environ.get(_ENABLE_ENV_NAME, "").lower() in {"1", "true", "yes"}


pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        not _docker_tests_enabled(os.environ),
        reason=f"set {_ENABLE_ENV_NAME}=1 to run Docker integration tests",
    ),
]


def _find_available_port() -> int:
    """Return an available localhost TCP port for the compose service."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _compose_environment(
    environ: Mapping[str, str],
    host_port: int,
) -> dict[str, str]:
    """Return a Docker Compose environment for the test service."""
    compose_environment = dict(environ)
    compose_environment["OMNIFETCH_DOCKER_PORT"] = str(host_port)
    return compose_environment


def _run_command(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess and fail with captured output on errors."""
    result = subprocess.run(
        command,
        cwd=_PROJECT_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "\n".join(
                (
                    f"command failed: {' '.join(command)}",
                    result.stdout,
                    result.stderr,
                )
            )
        )
    return result


def _compose_command(*arguments: str) -> tuple[str, ...]:
    """Return a Docker Compose command scoped to the integration project."""
    return ("docker", "compose", "--project-name", _PROJECT_NAME, *arguments)


def _read_json_url(url: str) -> dict[str, Any]:
    """Return one JSON response from the given URL."""
    with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT_S) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    assert isinstance(data, dict)
    return data


def _wait_for_health(host_port: int) -> dict[str, Any]:
    """Poll the container health endpoint until it responds."""
    deadline = time.monotonic() + _HEALTH_TIMEOUT_S
    health_url = f"http://127.0.0.1:{host_port}/health"
    while time.monotonic() < deadline:
        try:
            return _read_json_url(health_url)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(_POLL_INTERVAL_S)
    pytest.fail(f"container did not serve /health within {_HEALTH_TIMEOUT_S}s")


def _read_rest_validation_error(host_port: int) -> dict[str, Any]:
    """Return the REST validation error for an empty web_fetch payload."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{host_port}/web_fetch",
        data=b"{}",
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_S)
    except urllib.error.HTTPError as error:
        assert error.code == _HTTP_BAD_REQUEST
        data = json.loads(error.read().decode("utf-8"))
        assert isinstance(data, dict)
        return data
    pytest.fail("/web_fetch unexpectedly accepted an empty request")


async def _list_streamable_http_tools(host_port: int) -> set[str]:
    """Return tool names exposed by the streamable HTTP MCP endpoint."""
    async with Client(f"http://127.0.0.1:{host_port}/mcp/") as client:
        tools = await client.list_tools()
    return {tool.name for tool in tools}


async def test_docker_image_serves_http_mcp_health_and_rest() -> None:
    """Build the image, run HTTP mode, and check MCP plus REST endpoints."""
    host_port = _find_available_port()
    environment = _compose_environment(os.environ, host_port)
    _run_command(
        _compose_command("up", "-d", "--build"),
        environment=environment,
    )
    try:
        assert _wait_for_health(host_port) == {"status": "ok", "providers": 0}
        assert _read_rest_validation_error(host_port) == {
            "error": "url is required"
        }
        assert await _list_streamable_http_tools(host_port) == {
            "say_hello",
            "web_fetch",
        }
    finally:
        _run_command(
            _compose_command("stop"),
            environment=environment,
        )
