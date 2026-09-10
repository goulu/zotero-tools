"""Configuration management for Zotero Cleaner."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Automatically load .env if present
load_dotenv()


@dataclass
class ZoteroConfig:
    api_url: str = os.getenv("ZOTERO_API_URL", "http://localhost:23119/api")
    user_id: str = os.getenv("ZOTERO_USER_ID", "0")
    api_key: str | None = os.getenv("ZOTERO_API_KEY") or None
    timeout: float = float(os.getenv("ZOTERO_TIMEOUT", "30.0"))

    @property
    def server_url(self) -> str:
        """Root server URL (e.g., http://localhost:23119)."""
        url = self.api_url.rstrip("/")
        if url.endswith("/api"):
            return url[:-4]
        return url

    @property
    def base_user_url(self) -> str:
        """Base URL for the user's library."""
        url = self.api_url.rstrip("/")
        if "/users/" in url:
            return url
        return f"{url}/users/{self.user_id}"

    @property
    def items_url(self) -> str:
        """Items endpoint URL."""
        return f"{self.base_user_url}/items"

    @property
    def collections_url(self) -> str:
        """Collections endpoint URL."""
        return f"{self.base_user_url}/collections"


DEFAULT_CONFIG = ZoteroConfig()
