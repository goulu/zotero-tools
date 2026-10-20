"""Parser for Pour la Science magazine issue pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True)
class MagazineArticle:
    title: str
    url: str
    authors: tuple[str, ...]
    abstract: str
    section: str | None = None


@dataclass(frozen=True)
class MagazineIssue:
    url: str
    title: str
    issue_number: str | None
    articles: tuple[MagazineArticle, ...]


class _IssueHTMLParser(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.section_stack: list[bool] = []
        self.section_title: str | None = None
        self._in_section_header = False
        self._reading_section_title = False
        self._section_title_parts: list[str] = []
        self.issue_title_parts: list[str] = []
        self._issue_title_depth = 0
        self._article: dict[str, object] | None = None
        self._capture: str | None = None
        self._capture_depth = 0
        self._capture_parts: list[str] = []
        self._authors: list[str] = []
        self.articles: list[MagazineArticle] = []

    @property
    def in_issue_section(self) -> bool:
        return any(self.section_stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        capture_was_active = self._capture is not None
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "section":
            self.section_stack.append(
                "block" in classes and "articles" in classes
            )
        elif tag == "header" and self.in_issue_section:
            self.section_title = None
            self._in_section_header = True
            self._section_title_parts = []

        if (
            tag == "span"
            and self._in_section_header
            and not self._reading_section_title
        ):
            self._reading_section_title = True

        if tag in ("h1", "h2") and "title" in classes:
            if self._article is None and tag == "h1":
                self._issue_title_depth = 1
                self.issue_title_parts = []
            elif self._article is not None and tag == "h2":
                self._begin_capture("title", tag)

        if (
            self.in_issue_section
            and tag == "article"
            and self._article is None
        ):
            self._article = {
                "url": "",
                "title": "",
                "abstract": "",
                "authors": [],
                "section": self.section_title,
            }
            self._authors = []

        if self._article is not None:
            href = attributes.get("href")
            if tag == "a" and href and not self._article["url"]:
                self._article["url"] = urljoin(self.page_url, href)
            if tag == "p" and "chapo" in classes:
                self._begin_capture("abstract", tag)
            elif tag == "span" and "author" in classes:
                self._begin_capture("author", tag)

        if self._issue_title_depth and tag == "span":
            self._issue_title_depth += 1

        if capture_was_active and self._capture:
            self._capture_depth += 1

    def handle_endtag(self, tag: str):
        if self._capture:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                value = " ".join("".join(self._capture_parts).split())
                if self._article is not None:
                    if self._capture == "title":
                        self._article["title"] = value
                    elif self._capture == "abstract":
                        self._article["abstract"] = value
                    elif self._capture == "author" and value:
                        self._authors.append(value)
                self._capture = None
                self._capture_parts = []
                if tag == "article":
                    self._finish_article()

        if tag == "article" and self._article is not None:
            self._finish_article()

        if tag == "span" and self._reading_section_title:
            title = " ".join("".join(self._section_title_parts).split())
            if title:
                self.section_title = title
            self._reading_section_title = False
            self._section_title_parts = []

        if tag == "header" and self._in_section_header:
            self._in_section_header = False

        if tag == "section" and self.section_stack:
            self.section_stack.pop()

        if tag == "h1" and self._issue_title_depth:
            self._issue_title_depth = 0
        elif self._issue_title_depth:
            self.issue_title_parts.append(" ")

    def handle_data(self, data: str):
        if self._capture:
            self._capture_parts.append(data)
        if self._reading_section_title:
            self._section_title_parts.append(data)
        if self._issue_title_depth:
            self.issue_title_parts.append(data)

    def _begin_capture(self, capture: str, tag: str) -> None:
        if self._capture:
            return
        self._capture = capture
        self._capture_depth = 1
        self._capture_parts = []

    def _finish_article(self) -> None:
        if self._article is None:
            return
        title = " ".join(str(self._article["title"]).split())
        url = str(self._article["url"])
        if title and url and _is_site_url(url):
            self.articles.append(MagazineArticle(
                title=title,
                url=url,
                authors=tuple(dict.fromkeys(self._authors)),
                abstract=" ".join(str(self._article["abstract"]).split()),
                section=(
                    str(self._article["section"])
                    if self._article["section"]
                    else None
                ),
            ))
        self._article = None
        self._capture = None
        self._capture_depth = 0
        self._capture_parts = []


def parse_issue_page(html: str, url: str) -> MagazineIssue:
    if not _is_site_url(url):
        raise ValueError("Issue URL must belong to www.pourlascience.fr")

    parser = _IssueHTMLParser(url)
    parser.feed(html)
    articles_by_url = {article.url: article for article in parser.articles}
    issue_match = re.search(
        r"(?:hors-serie-)?pour-la-science-n(\d+)-\d+\.php",
        url,
        re.IGNORECASE,
    )
    issue_title = " ".join("".join(parser.issue_title_parts).split())
    return MagazineIssue(
        url=url,
        title=issue_title,
        issue_number=issue_match.group(1) if issue_match else None,
        articles=tuple(articles_by_url.values()),
    )


def _is_site_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.pourlascience.fr"
    )
