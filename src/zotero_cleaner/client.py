"""HTTP client for the local Zotero API."""

import re
import hashlib
from pathlib import Path
from typing import Any, Callable, Generator, Optional
import httpx

from zotero_cleaner.config import DEFAULT_CONFIG, ZoteroConfig
from zotero_cleaner.models import ZoteroItem


class ZoteroClientError(Exception):
    """Error related to Zotero API requests."""
    pass


def save_api_key_to_env(key: str, env_path: str = ".env"):
    """Save or update the local API key in the .env file."""
    path = Path(env_path)
    if not path.exists():
        path.write_text(f"ZOTERO_API_KEY={key}\n", encoding="utf-8")
        return

    content = path.read_text(encoding="utf-8")
    if re.search(r"^ZOTERO_API_KEY=.*$", content, flags=re.MULTILINE):
        new_content = re.sub(r"^ZOTERO_API_KEY=.*$", f"ZOTERO_API_KEY={key}", content, flags=re.MULTILINE)
    else:
        new_content = content.rstrip("\n") + f"\nZOTERO_API_KEY={key}\n"
    path.write_text(new_content, encoding="utf-8")


class ZoteroClient:
    def __init__(self, config: Optional[ZoteroConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self.server_id: Optional[str] = None
        self._authorization_attempted = False

        headers = {
            "Zotero-API-Version": "3",
            "Accept": "application/json",
        }
        if self.config.api_key:
            headers["Zotero-API-Key"] = self.config.api_key

        # Set base_url to host root (e.g., http://localhost:23119)
        self.client = httpx.Client(
            base_url=self.config.server_url,
            headers=headers,
            timeout=self.config.timeout,
        )

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _capture_server_id(self, resp: httpx.Response) -> Optional[str]:
        """Extract and store the Zotero-Server-ID returned in HTTP headers."""
        srv_id = resp.headers.get("Zotero-Server-ID")
        if srv_id:
            self.server_id = srv_id
        return srv_id

    def ensure_server_id(self) -> str:
        """Ensure Zotero-Server-ID is identified by probing Zotero if needed."""
        if self.server_id:
            return self.server_id

        try:
            resp = self.client.get(f"/api/users/{self.config.user_id}/items?limit=1")
            self._capture_server_id(resp)
            if self.server_id:
                return self.server_id
        except httpx.RequestError:
            pass

        try:
            resp = self.client.get("/connector/ping")
            self._capture_server_id(resp)
            if self.server_id:
                return self.server_id
        except httpx.RequestError:
            pass

        raise ZoteroClientError("Unable to retrieve Zotero-Server-ID. Please verify that Zotero is running.")

    def request_authorization(self, app_name: str = "Zotero Cleaner", timeout: float = 60.0) -> str:
        """
        Request a local write API key via POST /api/local/authorize.
        Triggers a confirmation prompt in the running Zotero application.
        """
        server_id = self.ensure_server_id()
        headers = {
            "Zotero-Server-ID": server_id,
            "Content-Type": "application/json",
        }
        payload = {"appName": app_name}

        try:
            resp = self.client.post(
                "/api/local/authorize",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            self._capture_server_id(resp)
        except httpx.TimeoutException:
            raise ZoteroClientError(
                "Authorization timed out (60s). Please confirm the popup in Zotero more promptly."
            )
        except httpx.RequestError as e:
            raise ZoteroClientError(f"Network error during authorization request: {e}")

        if resp.status_code != 200:
            raise ZoteroClientError(f"Authorization request rejected by Zotero: HTTP {resp.status_code} - {resp.text}")

        api_key = ""
        try:
            data = resp.json()
            if isinstance(data, dict):
                api_key = data.get("apiKey") or data.get("key") or ""
            elif isinstance(data, str):
                api_key = data
        except Exception:
            api_key = resp.text.strip()

        if not api_key:
            raise ZoteroClientError("API key not found in Zotero authorization response.")

        # Persist in memory and in .env
        self.config.api_key = api_key
        self.client.headers["Zotero-API-Key"] = api_key
        try:
            save_api_key_to_env(api_key)
        except Exception:
            pass

        return api_key

    def _request_authorization_once(self) -> None:
        if self._authorization_attempted:
            raise ZoteroClientError(
                "Zotero refused the write request after authorization; "
                "stopping to avoid repeated confirmation dialogs."
            )
        self._authorization_attempted = True
        print(
            "Waiting for manual authorization in Zotero...",
            flush=True,
        )
        self.request_authorization()

    def ping(self) -> dict[str, Any]:
        """Check connectivity with the local Zotero instance."""
        try:
            resp = self.client.get("/connector/ping")
            self._capture_server_id(resp)
            if resp.status_code == 200:
                version = resp.headers.get("X-Zotero-Version", "unknown")
                connector_ver = resp.headers.get("X-Zotero-Connector-API-Version", "")
                return {
                    "online": True,
                    "version": version,
                    "connector_api": connector_ver,
                    "url": str(resp.url),
                    "server_id": self.server_id,
                }
        except httpx.RequestError:
            pass

        try:
            resp = self.client.get(f"/api/users/{self.config.user_id}/items?limit=1")
            self._capture_server_id(resp)
            if resp.status_code in (200, 304):
                return {
                    "online": True,
                    "version": resp.headers.get("X-Zotero-Version", "unknown"),
                    "url": str(resp.url),
                    "total_items": int(resp.headers.get("Total-Results", "0")),
                    "server_id": self.server_id,
                }
        except httpx.RequestError as e:
            raise ZoteroClientError(f"Cannot connect to Zotero at {self.config.server_url}: {e}")

        raise ZoteroClientError(f"Zotero returned unexpected status: {resp.status_code}")

    def get_total_items(self, top_level: bool = True, item_type: Optional[str] = None) -> int:
        """Return the total number of items available."""
        endpoint = f"/api/users/{self.config.user_id}/items/top" if top_level else f"/api/users/{self.config.user_id}/items"
        params: dict[str, Any] = {"limit": 1}
        if item_type:
            params["itemType"] = item_type
        resp = self.client.get(endpoint, params=params)
        self._capture_server_id(resp)
        if resp.status_code != 200:
            raise ZoteroClientError(f"Failed to count items: HTTP {resp.status_code} - {resp.text}")
        return int(resp.headers.get("Total-Results", 0))

    def get_items(
        self,
        start: int = 0,
        limit: int = 100,
        top_level: bool = True,
        sort: str = "dateModified",
        direction: str = "desc",
        item_type: Optional[str] = None,
    ) -> list[ZoteroItem]:
        """Fetch a single page of Zotero items."""
        endpoint = f"/api/users/{self.config.user_id}/items/top" if top_level else f"/api/users/{self.config.user_id}/items"
        params: dict[str, Any] = {
            "start": start,
            "limit": min(limit, 100),
            "sort": sort,
            "direction": direction,
        }
        if item_type:
            params["itemType"] = item_type
        resp = self.client.get(endpoint, params=params)
        self._capture_server_id(resp)
        if resp.status_code != 200:
            raise ZoteroClientError(f"HTTP Error {resp.status_code}: {resp.text}")

        raw_items = resp.json()
        items = []
        for raw in raw_items:
            try:
                items.append(ZoteroItem.model_validate(raw))
            except Exception:
                continue
        return items

    def iter_all_items(
        self,
        batch_size: int = 100,
        max_items: Optional[int] = None,
        top_level: bool = True,
        item_type: Optional[str] = None,
        on_batch: Optional[Callable[[int, int], None]] = None,
    ) -> Generator[ZoteroItem, None, None]:
        """Paginated generator over library items."""
        total = self.get_total_items(top_level=top_level, item_type=item_type)
        limit_total = min(total, max_items) if max_items is not None else total

        fetched = 0
        start = 0

        while fetched < limit_total:
            current_batch = min(batch_size, limit_total - fetched)
            items = self.get_items(start=start, limit=current_batch, top_level=top_level, item_type=item_type)
            if not items:
                break

            for item in items:
                yield item
                fetched += 1
                if max_items and fetched >= max_items:
                    return

            start += len(items)
            if on_batch:
                on_batch(fetched, limit_total)

    def get_item(self, item_key: str) -> ZoteroItem:
        """Retrieve an individual item by its key."""
        resp = self.client.get(f"/api/users/{self.config.user_id}/items/{item_key}")
        self._capture_server_id(resp)
        if resp.status_code != 200:
            raise ZoteroClientError(f"Item {item_key} not found: HTTP {resp.status_code}")
        return ZoteroItem.model_validate(resp.json())

    def _prepare_write_headers(self, version: int) -> dict[str, str]:
        """Prepare mandatory HTTP headers for any write operation."""
        server_id = self.ensure_server_id()
        headers = {
            "If-Unmodified-Since-Version": str(version),
            "Zotero-Server-ID": server_id,
        }
        if self.config.api_key:
            headers["Zotero-API-Key"] = self.config.api_key
        return headers

    def update_item_data(self, item_key: str, version: int, patch_data: dict[str, Any]) -> dict[str, Any]:
        """Update an item with optimistic locking and required write headers."""
        headers = self._prepare_write_headers(version)
        headers["Content-Type"] = "application/json"

        resp = self.client.patch(
            f"/api/users/{self.config.user_id}/items/{item_key}",
            headers=headers,
            json=patch_data,
        )
        self._capture_server_id(resp)

        if resp.status_code in (401, 428):
            if resp.status_code == 401 or "API key required" in resp.text:
                self._request_authorization_once()
                headers = self._prepare_write_headers(version)
                headers["Content-Type"] = "application/json"
                resp = self.client.patch(
                    f"/api/users/{self.config.user_id}/items/{item_key}",
                    headers=headers,
                    json=patch_data,
                )
                self._capture_server_id(resp)

        if resp.status_code not in (200, 204):
            raise ZoteroClientError(f"Failed to update {item_key}: HTTP {resp.status_code} - {resp.text}")
        return resp.json() if resp.content else {}

    def attachment_file_exists(self, item_key: str) -> bool:
        """Return whether Zotero can serve the attachment file content."""
        try:
            with self.client.stream(
                "GET",
                f"/api/users/{self.config.user_id}/items/{item_key}/file",
                headers={"Range": "bytes=0-0"},
                follow_redirects=False,
            ) as resp:
                self._capture_server_id(resp)
                if resp.status_code in (200, 206, 302):
                    return True
                if resp.status_code == 404:
                    return False
                raise ZoteroClientError(
                    f"Unable to check attachment {item_key}: HTTP {resp.status_code}"
                )
        except httpx.RequestError as error:
            raise ZoteroClientError(
                f"Unable to check attachment {item_key}: {error}"
            )

    def restore_attachment_file(self, item: ZoteroItem, source: Path) -> None:
        """Restore an existing attachment from a verified local source PDF."""
        expected_md5 = item.md5
        if not expected_md5:
            raise ZoteroClientError(
                f"Attachment {item.key} has no MD5 checksum to verify restoration."
            )

        digest = hashlib.md5(source.read_bytes()).hexdigest()
        if digest.casefold() != expected_md5.casefold():
            raise ZoteroClientError(
                f"Source file checksum does not match attachment {item.key}."
            )

        headers = self._prepare_write_headers(item.version)
        headers.update(
            {
                "If-Match": expected_md5,
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
        metadata = {
            "md5": digest,
            "filename": source.name,
            "filesize": str(source.stat().st_size),
            "mtime": str(int(source.stat().st_mtime * 1000)),
        }
        response = self.client.post(
            f"/api/users/{self.config.user_id}/items/{item.key}/file",
            headers=headers,
            data=metadata,
        )
        self._capture_server_id(response)
        if response.status_code != 200:
            raise ZoteroClientError(
                f"Unable to authorize restoration of {item.key}: "
                f"HTTP {response.status_code} - {response.text}"
            )

        authorization = response.json()
        if authorization.get("exists"):
            return
        required_keys = ("url", "prefix", "suffix", "uploadKey")
        if not all(authorization.get(key) for key in required_keys):
            raise ZoteroClientError(
                f"Unexpected upload authorization response for {item.key}."
            )

        upload_response = self.client.post(
            authorization["url"],
            content=(
                authorization["prefix"].encode()
                + source.read_bytes()
                + authorization["suffix"].encode()
            ),
            headers={"Content-Type": authorization["contentType"]},
        )
        if upload_response.status_code != 201:
            raise ZoteroClientError(
                f"Unable to upload {item.key}: HTTP {upload_response.status_code} "
                f"- {upload_response.text}"
            )

        register_response = self.client.post(
            f"/api/users/{self.config.user_id}/items/{item.key}/file",
            headers=headers,
            data={"upload": authorization["uploadKey"]},
        )
        self._capture_server_id(register_response)
        if register_response.status_code != 204:
            raise ZoteroClientError(
                f"Unable to register restored file for {item.key}: "
                f"HTTP {register_response.status_code} - {register_response.text}"
            )

    def delete_item(self, item_key: str, version: int) -> bool:
        """Delete an item with version check, moving it to Zotero's Trash."""
        headers = self._prepare_write_headers(version)

        resp = self.client.delete(
            f"/api/users/{self.config.user_id}/items/{item_key}",
            headers=headers,
        )
        self._capture_server_id(resp)

        if resp.status_code in (401, 428):
            if resp.status_code == 401 or "API key required" in resp.text:
                self._request_authorization_once()
                headers = self._prepare_write_headers(version)
                resp = self.client.delete(
                    f"/api/users/{self.config.user_id}/items/{item_key}",
                    headers=headers,
                )
                self._capture_server_id(resp)

        if resp.status_code not in (200, 204):
            raise ZoteroClientError(f"Failed to delete {item_key}: HTTP {resp.status_code} - {resp.text}")
        return True

    def get_collections(self) -> list[dict[str, Any]]:
        """Retrieve user collections."""
        resp = self.client.get(f"/api/users/{self.config.user_id}/collections")
        self._capture_server_id(resp)
        if resp.status_code != 200:
            raise ZoteroClientError(f"Collections error: HTTP {resp.status_code}")
        return resp.json()

    def get_library_version(self) -> int:
        """Get current library version from Last-Modified-Version header."""
        resp = self.client.get(f"/api/users/{self.config.user_id}/items?limit=1")
        self._capture_server_id(resp)
        return int(resp.headers.get("Last-Modified-Version", 0))

    def create_items(
        self,
        items_data: list[dict[str, Any]],
        current_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Create one or more items in the library.
        Returns the parsed response dict containing 'successful', 'success', 'failed'.
        """
        if current_version is None:
            current_version = self.get_library_version()

        headers = self._prepare_write_headers(current_version)
        headers["Content-Type"] = "application/json"

        resp = self.client.post(
            f"/api/users/{self.config.user_id}/items",
            headers=headers,
            json=items_data,
        )
        self._capture_server_id(resp)

        if resp.status_code in (401, 428):
            if resp.status_code == 401 or "API key required" in resp.text:
                self._request_authorization_once()
                headers = self._prepare_write_headers(current_version)
                headers["Content-Type"] = "application/json"
                resp = self.client.post(
                    f"/api/users/{self.config.user_id}/items",
                    headers=headers,
                    json=items_data,
                )
                self._capture_server_id(resp)

        if resp.status_code == 412:
            # Version conflict, retry once with refreshed version
            current_version = self.get_library_version()
            headers = self._prepare_write_headers(current_version)
            headers["Content-Type"] = "application/json"
            resp = self.client.post(
                f"/api/users/{self.config.user_id}/items",
                headers=headers,
                json=items_data,
            )
            self._capture_server_id(resp)

        if resp.status_code not in (200, 201):
            raise ZoteroClientError(f"Failed to create items: HTTP {resp.status_code} - {resp.text}")

        return resp.json() if resp.content else {}

