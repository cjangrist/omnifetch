"""Self-hosted FastCRW endpoint using the shared scrape implementation."""

from __future__ import annotations

from omnifetch.fetch.providers.fastcrw import FastcrwFetchProvider
from omnifetch.fetch.shared.util import validate_api_key


class SelfHostedFastcrwFetchProvider(FastcrwFetchProvider):
    """Fetch markdown through the self-hosted FastCRW instance."""

    name = "fastcrw_selfhosted"
    description = "Scrape a single URL using self-hosted FastCRW."
    base_url = "https://crw.angrist.net"
    required_secrets = ("CRW_AUTH__API_KEYS",)

    def _api_key(self) -> str:
        """Use the first key from FastCRW's comma-separated allowlist."""
        configured = validate_api_key(
            self._secrets.get(self.required_secrets[0]), self.name
        )
        return validate_api_key(configured.split(",", 1)[0].strip(), self.name)
