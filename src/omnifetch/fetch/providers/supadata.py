"""Supadata fetch provider: YouTube URL to transcript markdown."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json, http_raw
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "SUPADATA_API_KEY"
_TIMEOUT_MS = 60_000
_POLL_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 1.5
_MIN_VIDEO_PATH_PARTS = 2
_HTTP_ACCEPTED = 202


class _SupadataTranscriptResponse(BaseModel):
    """Immediate Supadata transcript response."""

    model_config = ConfigDict(extra="ignore")

    content: str = ""


class _SupadataAsyncJobResponse(BaseModel):
    """Supadata async transcript job response."""

    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("jobId", "job_id"),
    )


class _SupadataJobStatusResponse(BaseModel):
    """Supadata async transcript job status response."""

    model_config = ConfigDict(extra="ignore")

    status: str
    content: str | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class _SupadataRequest:
    """Shared Supadata request dependencies."""

    provider: str
    client: httpx.AsyncClient
    api_key: str
    base_url: str
    timeout_ms: int


def _extract_video_id(url: str) -> str | None:
    """Return the YouTube video ID embedded in ``url``, if present."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        return video_id or None
    if hostname not in {"youtube.com", "m.youtube.com"}:
        return None

    query_video_ids = parse_qs(parsed.query).get("v", [])
    if query_video_ids and query_video_ids[0]:
        return query_video_ids[0]

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if len(path_parts) >= _MIN_VIDEO_PATH_PARTS and path_parts[0] in {
        "embed",
        "shorts",
        "live",
    }:
        return path_parts[1]
    return None


async def _poll_job(request: _SupadataRequest, job_id: str) -> str:
    """Poll a Supadata transcript job until content is ready."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (request.timeout_ms / 1000)
    poll_url = f"{request.base_url}/transcript/{job_id}"

    while loop.time() < deadline:
        status = await http_json(
            request.client,
            request.provider,
            poll_url,
            model=_SupadataJobStatusResponse,
            headers={"x-api-key": request.api_key},
            timeout_s=_POLL_TIMEOUT_S,
        )
        if status.status == "completed" and status.content:
            return status.content
        if status.status == "completed":
            raise ValueError(
                "Supadata transcript job completed without content"
            )
        if status.status == "failed":
            raise ValueError(f"Supadata transcript job failed: {status.error}")

        remaining_s = max(0.0, deadline - loop.time())
        await asyncio.sleep(min(_POLL_INTERVAL_S, remaining_s))

    raise TimeoutError("Supadata transcript job timed out")


async def _fetch_transcript(request: _SupadataRequest, url: str) -> str:
    """Fetch or poll a Supadata transcript for ``url``."""
    query = urlencode(
        {"url": url, "text": "true", "mode": "auto", "lang": "en"}
    )
    raw, status = await http_raw(
        request.client,
        request.provider,
        f"{request.base_url}/transcript?{query}",
        headers={"x-api-key": request.api_key},
        timeout_s=request.timeout_ms / 1000,
        expected_statuses=(_HTTP_ACCEPTED,),
    )
    if status == _HTTP_ACCEPTED:
        job = _SupadataAsyncJobResponse.model_validate_json(raw)
        return await _poll_job(request, job.job_id)

    data = _SupadataTranscriptResponse.model_validate_json(raw)
    if not data.content:
        raise ValueError("Supadata returned no transcript for this video")
    return data.content


class SupadataFetchProvider(FetchProvider):
    """Fetch YouTube transcripts using Supadata."""

    name = "supadata"
    description = "Fetch YouTube transcripts using Supadata API."
    base_url = "https://api.supadata.ai/v1"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Supadata and return transcript markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        video_id = _extract_video_id(url)
        if video_id is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                f"Not a YouTube URL: {url[:200]}",
                self.name,
            )

        try:
            request = _SupadataRequest(
                self.name,
                self._client,
                api_key,
                self.base_url,
                self.timeout_ms,
            )
            content = await _fetch_transcript(
                request,
                url,
            )
            return FetchResult(
                url=url,
                title=f"YouTube Transcript: {video_id}",
                content=f"# YouTube Video Transcript\n\n{content}",
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(
                error,
                self.name,
                "fetch YouTube transcript",
            )
