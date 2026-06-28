"""Fetch-provider environment contract.

Provider secrets keep their upstream environment variable names so existing
Doppler or ``.env`` setups work unchanged. Endpoint, timeout, and availability
declarations live on provider classes.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDER_ENV_NAMES = (
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "JINA_API_KEY",
    "YOU_API_KEY",
    "BRIGHT_DATA_API_KEY",
    "BRIGHT_DATA_ZONE",
    "LINKUP_API_KEY",
    "DIFFBOT_TOKEN",
    "SOCIAVAULT_API_KEY",
    "SPIDER_CLOUD_API_TOKEN",
    "SCRAPFLY_API_KEY",
    "SCRAPEGRAPHAI_API_KEY",
    "SCRAPE_DO_API_TOKEN",
    "SCRAPELESS_API_KEY",
    "OPENGRAPH_IO_API_KEY",
    "SCRAPINGBEE_API_KEY",
    "SCRAPERAPI_API_KEY",
    "ZYTE_API_KEY",
    "SCRAPINGANT_API_KEY",
    "OXYLABS_WEB_SCRAPER_USERNAME",
    "OXYLABS_WEB_SCRAPER_PASSWORD",
    "OLOSTEP_API_KEY",
    "DECODO_WEB_SCRAPING_API_KEY",
    "SCRAPPEY_API_KEY",
    "LEADMAGIC_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_EMAIL",
    "CLOUDFLARE_API_KEY",
    "SERPAPI_API_KEY",
    "SUPADATA_API_KEY",
    "GITHUB_API_KEY",
    "KIMI_API_KEY",
)


class ProviderSecrets(BaseSettings):
    """All fetch-provider secrets, read once and frozen."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    tavily_api_key: str | None = Field(
        default=None, validation_alias="TAVILY_API_KEY"
    )
    firecrawl_api_key: str | None = Field(
        default=None, validation_alias="FIRECRAWL_API_KEY"
    )
    jina_api_key: str | None = Field(
        default=None, validation_alias="JINA_API_KEY"
    )
    you_api_key: str | None = Field(
        default=None, validation_alias="YOU_API_KEY"
    )
    bright_data_api_key: str | None = Field(
        default=None, validation_alias="BRIGHT_DATA_API_KEY"
    )
    bright_data_zone: str = Field(
        default="unblocker", validation_alias="BRIGHT_DATA_ZONE"
    )
    linkup_api_key: str | None = Field(
        default=None, validation_alias="LINKUP_API_KEY"
    )
    diffbot_token: str | None = Field(
        default=None, validation_alias="DIFFBOT_TOKEN"
    )
    sociavault_api_key: str | None = Field(
        default=None, validation_alias="SOCIAVAULT_API_KEY"
    )
    spider_cloud_api_token: str | None = Field(
        default=None, validation_alias="SPIDER_CLOUD_API_TOKEN"
    )
    scrapfly_api_key: str | None = Field(
        default=None, validation_alias="SCRAPFLY_API_KEY"
    )
    scrapegraphai_api_key: str | None = Field(
        default=None, validation_alias="SCRAPEGRAPHAI_API_KEY"
    )
    scrape_do_api_token: str | None = Field(
        default=None, validation_alias="SCRAPE_DO_API_TOKEN"
    )
    scrapeless_api_key: str | None = Field(
        default=None, validation_alias="SCRAPELESS_API_KEY"
    )
    opengraph_io_api_key: str | None = Field(
        default=None, validation_alias="OPENGRAPH_IO_API_KEY"
    )
    scrapingbee_api_key: str | None = Field(
        default=None, validation_alias="SCRAPINGBEE_API_KEY"
    )
    scraperapi_api_key: str | None = Field(
        default=None, validation_alias="SCRAPERAPI_API_KEY"
    )
    zyte_api_key: str | None = Field(
        default=None, validation_alias="ZYTE_API_KEY"
    )
    scrapingant_api_key: str | None = Field(
        default=None, validation_alias="SCRAPINGANT_API_KEY"
    )
    oxylabs_username: str | None = Field(
        default=None, validation_alias="OXYLABS_WEB_SCRAPER_USERNAME"
    )
    oxylabs_password: str | None = Field(
        default=None, validation_alias="OXYLABS_WEB_SCRAPER_PASSWORD"
    )
    olostep_api_key: str | None = Field(
        default=None, validation_alias="OLOSTEP_API_KEY"
    )
    decodo_api_key: str | None = Field(
        default=None, validation_alias="DECODO_WEB_SCRAPING_API_KEY"
    )
    scrappey_api_key: str | None = Field(
        default=None, validation_alias="SCRAPPEY_API_KEY"
    )
    leadmagic_api_key: str | None = Field(
        default=None, validation_alias="LEADMAGIC_API_KEY"
    )
    cloudflare_account_id: str | None = Field(
        default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID"
    )
    cloudflare_email: str | None = Field(
        default=None, validation_alias="CLOUDFLARE_EMAIL"
    )
    cloudflare_api_key: str | None = Field(
        default=None, validation_alias="CLOUDFLARE_API_KEY"
    )
    serpapi_api_key: str | None = Field(
        default=None, validation_alias="SERPAPI_API_KEY"
    )
    supadata_api_key: str | None = Field(
        default=None, validation_alias="SUPADATA_API_KEY"
    )
    github_api_key: str | None = Field(
        default=None, validation_alias="GITHUB_API_KEY"
    )
    kimi_api_key: str | None = Field(
        default=None, validation_alias="KIMI_API_KEY"
    )
