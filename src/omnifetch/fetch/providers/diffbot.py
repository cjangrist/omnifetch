"""Diffbot Article API fetch provider: URL to structured article text."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "DIFFBOT_TOKEN"
_TIMEOUT_MS = 30_000


class _DiffbotImage(BaseModel):
    """Typed subset of Diffbot article image metadata."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    caption: str | None = None


class _DiffbotArticle(BaseModel):
    """Typed subset of one Diffbot article object."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    text: str | None = None
    author: str | None = None
    date: str | None = None
    site_name: str | None = Field(default=None, validation_alias="siteName")
    images: list[_DiffbotImage] = Field(default_factory=list)


class _DiffbotArticleResponse(BaseModel):
    """Typed subset of the Diffbot Article API response."""

    model_config = ConfigDict(extra="ignore")

    objects: list[_DiffbotArticle] = Field(default_factory=list)


def _metadata_for_article(
    article: _DiffbotArticle,
) -> dict[str, object] | None:
    """Return provider metadata from present Diffbot article fields."""
    metadata: dict[str, object] = {
        key: value
        for key, value in (
            ("author", article.author),
            ("date", article.date),
            ("site_name", article.site_name),
            (
                "image_count",
                len(article.images) if article.images else None,
            ),
        )
        if value
    }
    return metadata or None


class DiffbotFetchProvider(FetchProvider):
    """Extract structured article text using Diffbot Article API."""

    name = "diffbot"
    description = (
        "Extract structured article content using Diffbot Article API. "
        "Rich metadata including author, date, and images."
    )
    base_url = "https://api.diffbot.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Diffbot and return normalized article text."""
        token = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v3/article",
                model=_DiffbotArticleResponse,
                params={"token": token, "url": url},
                timeout_s=self.timeout_s,
            )
            article = data.objects[0] if data.objects else None
            if article is None or not article.text:
                raise ValueError("Diffbot returned no article content")

            return FetchResult(
                url=url,
                title=article.title or "",
                content=article.text,
                source_provider=self.name,
                metadata=_metadata_for_article(article),
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
