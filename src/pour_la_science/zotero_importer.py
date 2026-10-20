"""Import Pour la Science issue articles into Zotero."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from zotero_cleaner.client import ZoteroClient
from zotero_cleaner.models import ZoteroItem

from .archives import MagazineArticle, MagazineIssue


@dataclass
class ArticleImportResult:
    article: MagazineArticle
    status: str
    detail: str = ""


@dataclass
class IssueImportSummary:
    issue: MagazineIssue
    dry_run: bool
    results: list[ArticleImportResult] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(result.status == "added" for result in self.results)

    @property
    def would_add(self) -> int:
        return sum(result.status == "would_add" for result in self.results)

    @property
    def duplicates(self) -> int:
        return sum(result.status == "duplicate" for result in self.results)

    @property
    def errors(self) -> int:
        return sum(result.status == "error" for result in self.results)


class PourLaScienceZoteroImporter:
    def __init__(self, zotero_client: ZoteroClient):
        self.zotero_client = zotero_client

    def import_issue(
        self,
        issue: MagazineIssue,
        dry_run: bool = False,
    ) -> IssueImportSummary:
        existing_urls, existing_title_authors = self._load_existing_keys()
        summary = IssueImportSummary(issue=issue, dry_run=dry_run)
        pending: list[MagazineArticle] = []

        for article in issue.articles:
            url_key = _normalize_url(article.url)
            title_key = _normalize_text(article.title)
            author_keys = {
                _normalize_text(author)
                for author in article.authors
            }
            if (
                url_key in existing_urls
                or any(
                    (title_key, author) in existing_title_authors
                    for author in author_keys
                )
            ):
                summary.results.append(ArticleImportResult(
                    article=article,
                    status="duplicate",
                    detail="Already present in Zotero",
                ))
            elif dry_run:
                summary.results.append(ArticleImportResult(
                    article=article,
                    status="would_add",
                    detail="Would create journal article",
                ))
            else:
                pending.append(article)

        for start in range(0, len(pending), 50):
            batch = pending[start:start + 50]
            payloads = [
                self._to_zotero_payload(issue, article)
                for article in batch
            ]
            try:
                response = self.zotero_client.create_items(payloads)
            except Exception as error:
                summary.results.extend(
                    ArticleImportResult(article, "error", str(error))
                    for article in batch
                )
                continue

            successful = response.get("successful", {})
            failed = response.get("failed", {})
            for index, article in enumerate(batch):
                key = str(index)
                if key in successful:
                    summary.results.append(ArticleImportResult(
                        article=article,
                        status="added",
                        detail=str(successful[key].get("key", "")),
                    ))
                else:
                    summary.results.append(ArticleImportResult(
                        article=article,
                        status="error",
                        detail=str(
                            failed.get(key, "Zotero did not create this item")
                        ),
                    ))

        return summary

    def _load_existing_keys(self) -> tuple[set[str], set[tuple[str, str]]]:
        urls: set[str] = set()
        title_authors: set[tuple[str, str]] = set()
        for item in self.zotero_client.iter_all_items(top_level=True):
            if not isinstance(item, ZoteroItem):
                continue
            item_url = getattr(item.data, "url", None)
            if item_url:
                urls.add(_normalize_url(item_url))
            title_key = _normalize_text(item.title)
            for creator in item.data.creators:
                creator_name = creator.name or " ".join(filter(None, (
                    creator.firstName,
                    creator.lastName,
                )))
                if creator_name:
                    title_authors.add((
                        title_key,
                        _normalize_text(creator_name),
                    ))
        return urls, title_authors

    @staticmethod
    def _to_zotero_payload(
        issue: MagazineIssue,
        article: MagazineArticle,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "itemType": "journalArticle",
            "title": article.title,
            "publicationTitle": "Pour la Science",
            "url": article.url,
            "creators": [
                {"creatorType": "author", "name": author}
                for author in article.authors
            ],
        }
        if issue.issue_number:
            payload["issue"] = issue.issue_number
        if article.abstract:
            payload["abstractNote"] = article.abstract
        return payload


def _normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
    ))


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w\s]", "", without_marks).split())
