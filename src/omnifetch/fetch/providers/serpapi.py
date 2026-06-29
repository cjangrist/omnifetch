"""SerpAPI fetch provider: YouTube URL to transcript markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key
from omnifetch.fetch.shared.youtube import extract_youtube_video_id

_API_KEY_ENV_NAME = "SERPAPI_API_KEY"
_TIMEOUT_MS = 30_000


class _TranscriptEntry(BaseModel):
    """One SerpAPI YouTube transcript segment."""

    model_config = ConfigDict(extra="ignore")

    start: float
    end: float
    snippet: str


class _SearchMetadata(BaseModel):
    """SerpAPI search metadata subset."""

    model_config = ConfigDict(extra="ignore")

    status: str | None = None


class _SerpapiTranscriptResponse(BaseModel):
    """Typed subset of SerpAPI YouTube transcript responses."""

    model_config = ConfigDict(extra="ignore")

    transcript: list[_TranscriptEntry] | None = None
    search_metadata: _SearchMetadata | None = None
    error: str | None = None


class SerpapiFetchProvider(FetchProvider):
    """Fetch YouTube video transcripts using SerpAPI."""

    name = "serpapi"
    description = (
        "Fetch YouTube video transcripts using SerpAPI YouTube Transcript "
        "engine."
    )
    base_url = "https://serpapi.com/search.json"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through SerpAPI and return transcript markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        video_id = extract_youtube_video_id(url)
        if video_id is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                f"Not a YouTube URL: {url[:200]}",
                self.name,
            )

        try:
            params = {
                "engine": "youtube_video_transcript",
                "v": video_id,
                "api_key": api_key,
            }
            data = await http_json(
                self._client,
                self.name,
                self.base_url,
                model=_SerpapiTranscriptResponse,
                params=params,
                timeout_s=self.timeout_s,
            )
            if data.error:
                raise ValueError(f"SerpAPI error: {data.error}")
            if not data.transcript:
                raise ValueError(
                    "SerpAPI returned no transcript for this video"
                )

            transcript_text = " ".join(
                segment.snippet for segment in data.transcript
            )
            return FetchResult(
                url=url,
                title=f"YouTube Transcript: {video_id}",
                content=f"# YouTube Video Transcript\n\n{transcript_text}",
                source_provider=self.name,
                metadata={
                    "video_id": video_id,
                    "transcript_segments": len(data.transcript),
                },
            )
        except Exception as error:
            handle_provider_error(
                error,
                self.name,
                "fetch YouTube transcript",
            )
