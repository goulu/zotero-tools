"""Session and authentication client for Pour la Science."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .config import PourLaScienceConfig


class PourLaScienceError(RuntimeError):
    """Base error for site access and authentication failures."""


class AuthenticationError(PourLaScienceError):
    """Raised when the SSO flow does not establish a session."""


class _LoginLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag != "a":
            return
        attributes = dict(attrs)
        if attributes.get("id") == "connect_link":
            self.href = attributes.get("href")


class _LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[dict[str, object]] = []
        self._current_form: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = dict(attrs)
        if tag == "form":
            self._current_form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "get").lower(),
                "fields": {},
            }
            self.forms.append(self._current_form)
        elif tag == "input" and self._current_form is not None:
            name = attributes.get("name")
            if name:
                fields = self._current_form["fields"]
                fields[name] = attributes.get("value", "")

    def handle_endtag(self, tag: str):
        if tag == "form":
            self._current_form = None


class PourLaScienceClient:
    ARCHIVES_HOST = "www.pourlascience.fr"
    LOGIN_HOST = "connexion.groupepourlascience.fr"

    def __init__(
        self,
        config: PourLaScienceConfig | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.config = config or PourLaScienceConfig.from_env()
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=self.config.timeout,
            follow_redirects=True,
            headers={"User-Agent": "ZoteroTools/0.1.0"},
        )
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def close(self) -> None:
        if self._owns_client:
            self._http.close()
        self._authenticated = False

    def __enter__(self) -> "PourLaScienceClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def login(self) -> None:
        if not self.config.email or not self.config.password:
            raise AuthenticationError(
                "Set POURLASCIENCE_EMAIL and POURLASCIENCE_PASSWORD in .env."
            )

        archives = self._http.get(self.config.archives_url)
        archives.raise_for_status()
        link_parser = _LoginLinkParser()
        link_parser.feed(archives.text)
        if not link_parser.href:
            raise AuthenticationError(
                "Could not find the Pour la Science login link on the archives page."
            )

        login_url = urljoin(str(archives.url), link_parser.href)
        self._require_host(login_url, self.LOGIN_HOST)
        login_page = self._http.get(login_url)
        login_page.raise_for_status()

        form_parser = _LoginFormParser()
        form_parser.feed(login_page.text)
        form = next(
            (
                candidate
                for candidate in form_parser.forms
                if self._is_login_form(candidate)
            ),
            None,
        )
        if form is None:
            raise AuthenticationError(
                "The SSO login form was not found; the site may have changed."
            )

        action = urljoin(str(login_page.url), str(form["action"]))
        self._require_host(action, "sso.qiota.com")
        fields = dict(form["fields"])
        fields["email"] = self.config.email
        fields["password"] = self.config.password
        response = self._http.post(
            action,
            data=fields,
            headers={"Referer": str(login_page.url)},
        )
        response.raise_for_status()

        if self._is_login_form_page(response.text):
            raise AuthenticationError(
                "Login was not accepted; credentials may be invalid or additional "
                "verification may be required."
            )
        self._authenticated = True

    def get_archives_page(self):
        if not self._authenticated:
            raise AuthenticationError("Call login() before requesting archive pages.")
        response = self._http.get(self.config.archives_url)
        response.raise_for_status()
        return response

    def get_issue_page(self, url: str):
        if not self._authenticated:
            raise AuthenticationError("Call login() before requesting issue pages.")
        self._require_host(url, self.ARCHIVES_HOST)
        response = self._http.get(url)
        response.raise_for_status()
        self._require_host(str(response.url), self.ARCHIVES_HOST)
        return response

    @staticmethod
    def _is_login_form(form: dict[str, object]) -> bool:
        fields = form.get("fields", {})
        return (
            form.get("method") == "post"
            and isinstance(fields, dict)
            and "email" in fields
            and "password" in fields
        )

    @classmethod
    def _is_login_form_page(cls, html: str) -> bool:
        parser = _LoginFormParser()
        parser.feed(html)
        return any(cls._is_login_form(form) for form in parser.forms)

    @staticmethod
    def _require_host(url: str, expected_host: str) -> None:
        if urlparse(url).hostname != expected_host:
            raise PourLaScienceError(
                f"Unexpected authentication host: {urlparse(url).hostname}"
            )
