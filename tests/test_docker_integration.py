"""Docker integration test for the container runtime."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import docker
import httpx
import pytest
from docker.errors import BuildError, DockerException, ImageNotFound, NotFound
from fastmcp import Client

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENABLE_ENV_NAME = "OMNIFETCH_RUN_DOCKER_TESTS"
_DOCKERFILE = "Dockerfile"
_IMAGE_REPOSITORY = "omnifetch-under-test"
_CONTAINER_PORT = 8000
_HEALTH_PATH = "/health"
_HEALTH_TIMEOUT_S = 90.0
_REQUEST_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.5
_HTTP_BAD_REQUEST = 400
_BUILD_LOG_LIMIT = 12_000
_CONTAINER_LOG_LIMIT = 200


def _docker_tests_enabled() -> bool:
    """Return whether the Docker integration test should run."""
    return os.environ.get(_ENABLE_ENV_NAME, "").lower() in {"1", "true", "yes"}


pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.skipif(
        not _docker_tests_enabled(),
        reason=f"set {_ENABLE_ENV_NAME}=1 to run Docker integration tests",
    ),
]


def _format_build_log(build_log: object) -> str:
    """Return the last part of a Docker build log as readable text."""
    lines: list[str] = []
    if not isinstance(build_log, Iterable) or isinstance(
        build_log, (bytes, str)
    ):
        return str(build_log)[-_BUILD_LOG_LIMIT:]

    for chunk in build_log:
        if not isinstance(chunk, dict):
            lines.append(str(chunk))
            continue

        stream = chunk.get("stream")
        error = chunk.get("error")
        aux = chunk.get("aux")
        if isinstance(stream, str):
            lines.append(stream.rstrip())
        elif isinstance(error, str):
            lines.append(error.rstrip())
        elif aux is not None:
            lines.append(str(aux))

    return "\n".join(line for line in lines if line)[-_BUILD_LOG_LIMIT:]


def _container_logs(container: Any) -> str:
    """Return recent container logs for assertion messages."""
    try:
        logs = container.logs(tail=_CONTAINER_LOG_LIMIT)
    except DockerException as error:
        return f"<could not read container logs: {error}>"
    if isinstance(logs, bytes):
        return logs.decode("utf-8", errors="replace")
    return str(logs)


def _health_log(container: Any) -> str:
    """Return recent Docker healthcheck output."""
    container.reload()
    health = container.attrs.get("State", {}).get("Health", {})
    entries = health.get("Log", [])
    lines = [
        f"exit={entry.get('ExitCode')}: {entry.get('Output', '').strip()}"
        for entry in entries[-5:]
    ]
    return "\n".join(lines)


def _wait_until_docker_healthy(container: Any) -> None:
    """Wait until Docker reports the container as healthy."""
    deadline = time.monotonic() + _HEALTH_TIMEOUT_S
    last_status: str | None = None
    while time.monotonic() < deadline:
        container.reload()
        state = container.attrs.get("State", {})
        if state.get("Status") == "exited":
            pytest.fail(
                "Container exited before becoming healthy.\n\n"
                f"Container logs:\n{_container_logs(container)}"
            )

        health = state.get("Health")
        if health is None:
            pytest.fail(
                "Container has no Docker HEALTHCHECK. "
                "Add HEALTHCHECK to the Dockerfile."
            )

        last_status = health.get("Status")
        if last_status == "healthy":
            return
        time.sleep(_POLL_INTERVAL_S)

    pytest.fail(
        f"Container did not become healthy within {_HEALTH_TIMEOUT_S}s. "
        f"Last Docker health status: {last_status}\n\n"
        f"Docker health log:\n{_health_log(container)}\n\n"
        f"Container logs:\n{_container_logs(container)}"
    )


def _published_host_port(container: Any) -> int:
    """Return the random host port published for the app port."""
    container.reload()
    port_key = f"{_CONTAINER_PORT}/tcp"
    network_settings = container.attrs.get("NetworkSettings", {})
    ports = network_settings.get("Ports", {})
    bindings = ports.get(port_key)
    if not isinstance(bindings, list) or not bindings:
        pytest.fail(f"No host port was published for container port {port_key}")

    host_port = bindings[0].get("HostPort")
    if not isinstance(host_port, str):
        pytest.fail(f"Invalid host port binding for container port {port_key}")
    return int(host_port)


def _handle_unavailable_docker(error: DockerException) -> None:
    """Fail in GitHub Actions and skip only on local machines without Docker."""
    message = f"Docker daemon is not available: {error}"
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def docker_client() -> Iterator[Any]:
    """Return a Docker SDK client connected to the local daemon."""
    client = docker.from_env()
    try:
        client.ping()
    except DockerException as error:
        _handle_unavailable_docker(error)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def built_image(docker_client: Any) -> Iterator[str]:
    """Build the repository Docker image and remove it after the test run."""
    tag = f"{_IMAGE_REPOSITORY}:{uuid.uuid4().hex}"
    image_created = False
    try:
        docker_client.images.build(
            path=str(_PROJECT_ROOT),
            dockerfile=_DOCKERFILE,
            tag=tag,
            rm=True,
            forcerm=True,
        )
        image_created = True
    except BuildError as error:
        pytest.fail(
            "Docker image build failed.\n\n"
            f"Build log:\n{_format_build_log(error.build_log)}"
        )

    try:
        yield tag
    finally:
        if image_created:
            with suppress(ImageNotFound):
                docker_client.images.remove(image=tag, force=True)


@pytest.fixture
def app_container(docker_client: Any, built_image: str) -> Iterator[Any]:
    """Run the built image and remove the container after the test."""
    container = None
    try:
        container = docker_client.containers.run(
            built_image,
            detach=True,
            ports={f"{_CONTAINER_PORT}/tcp": None},
            labels={
                "created-by": "pytest",
                "test-kind": "docker-integration",
            },
            auto_remove=False,
        )
        _wait_until_docker_healthy(container)
        yield container
    finally:
        if container is not None:
            with suppress(NotFound):
                container.remove(force=True)


async def _read_json_response(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, Any]:
    """Return one JSON object from an HTTP response."""
    response = await client.get(url)
    assert response.status_code == httpx.codes.OK, response.text
    data = response.json()
    assert isinstance(data, dict)
    return data


async def _read_rest_validation_error(
    client: httpx.AsyncClient,
    host_port: int,
    container: Any,
) -> dict[str, Any]:
    """Return the REST validation error for an empty web_fetch payload."""
    response = await client.post(
        f"http://127.0.0.1:{host_port}/web_fetch",
        json={},
    )
    assert response.status_code == _HTTP_BAD_REQUEST, (
        f"Unexpected /web_fetch response: {response.status_code} "
        f"{response.text}\n\nContainer logs:\n{_container_logs(container)}"
    )
    data = response.json()
    assert isinstance(data, dict)
    return data


async def _list_streamable_http_tools(host_port: int) -> set[str]:
    """Return tool names exposed by the streamable HTTP MCP endpoint."""
    async with Client(f"http://127.0.0.1:{host_port}/mcp/") as client:
        tools = await client.list_tools()
    return {tool.name for tool in tools}


async def test_docker_image_serves_http_mcp_health_and_rest(
    app_container: Any,
) -> None:
    """Build the image, run HTTP mode, and check MCP plus REST endpoints."""
    host_port = _published_host_port(app_container)
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as http_client:
        health_url = f"http://127.0.0.1:{host_port}{_HEALTH_PATH}"
        assert await _read_json_response(http_client, health_url) == {
            "status": "ok",
            "providers": 0,
        }
        assert await _read_rest_validation_error(
            http_client,
            host_port,
            app_container,
        ) == {"error": "url is required"}
    assert await _list_streamable_http_tools(host_port) == {
        "say_hello",
        "web_fetch",
    }
