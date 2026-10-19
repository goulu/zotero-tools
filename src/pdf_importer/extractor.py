"""PDF metadata extraction pipeline.

Combines:
1. DOI discovery in PDF text/metadata + CrossRef API bibliographic lookup
2. PDF internal XMP/Info metadata (Title, Author, CreationDate)
3. Filename & directory structure heuristics (e.g. Journal/Year/Author - Title.pdf)
"""

import hashlib
import logging
import re
import warnings
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional
import httpx
from pypdf import PdfReader

# Suppress pypdf internal warnings and fontTools messages
logging.getLogger("pypdf").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="pypdf")

DOI_REGEX = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


@dataclass
class ExtractedMetadata:
    title: str
    creators: list[dict[str, str]] = field(default_factory=list)
    date: Optional[str] = None
    publication_title: Optional[str] = None
    doi: Optional[str] = None
    isbn: Optional[str] = None
    item_type: str = "journalArticle"
    abstract_note: Optional[str] = None
    url: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    source: str = "fallback"  # 'crossref', 'pdf_meta', 'filename_heuristic', 'fallback'

    def to_zotero_item_payload(self) -> dict[str, Any]:
        """Convert extracted metadata into Zotero API item structure."""
        payload: dict[str, Any] = {
            "itemType": self.item_type,
            "title": self.title,
        }
        if self.creators:
            payload["creators"] = self.creators
        if self.date:
            payload["date"] = self.date
        if self.publication_title:
            payload["publicationTitle"] = self.publication_title
        if self.doi:
            payload["DOI"] = self.doi
        if self.isbn:
            payload["ISBN"] = self.isbn
        if self.abstract_note:
            payload["abstractNote"] = self.abstract_note
        if self.url:
            payload["url"] = self.url
        if self.volume:
            payload["volume"] = self.volume
        if self.issue:
            payload["issue"] = self.issue
        if self.pages:
            payload["pages"] = self.pages
        return payload


def compute_file_md5(file_path: Path, block_size: int = 65536) -> str:
    """Compute MD5 checksum of a file efficiently in chunks."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(block_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def clean_doi(raw_doi: str) -> str:
    """Normalize and clean a DOI string."""
    doi = raw_doi.strip().rstrip(".,;)>]}")
    if doi.lower().startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    elif doi.lower().startswith("http://doi.org/"):
        doi = doi[len("http://doi.org/"):]
    elif doi.lower().startswith("doi:"):
        doi = doi[len("doi:"):]
    return doi.strip().strip("/")


def find_doi_in_text(text: str) -> Optional[str]:
    """Search for a DOI in a text snippet."""
    match = DOI_REGEX.search(text)
    if match:
        return clean_doi(match.group(1))
    return None


def fetch_crossref_metadata(doi: str, timeout: float = 6.0) -> Optional[ExtractedMetadata]:
    """Retrieve bibliographic metadata from CrossRef API for a given DOI."""
    cleaned = clean_doi(doi)
    if not cleaned:
        return None

    url = f"https://api.crossref.org/works/{cleaned}"
    headers = {"User-Agent": "ZoteroTools/0.1.0 (mailto:zotero-tools@local)"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            message = data.get("message", {})
            if not message:
                return None

            return _parse_crossref_message(message, doi=cleaned)
    except Exception:
        return None


def fetch_crossref_metadata_by_title(
    title: str,
    timeout: float = 6.0,
) -> Optional[ExtractedMetadata]:
    """Search Crossref by title and accept only a close title match."""
    normalized_title = re.sub(r"[^\w\s]", "", title.lower())
    normalized_title = " ".join(normalized_title.split())
    if not normalized_title:
        return None

    url = "https://api.crossref.org/works"
    headers = {"User-Agent": "ZoteroTools/0.1.0 (mailto:zotero-tools@local)"}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                url,
                params={"query.title": title, "rows": 5},
                headers=headers,
            )
            if response.status_code != 200:
                return None
            items = response.json().get("message", {}).get("items", [])

        matches = []
        for item in items:
            titles = item.get("title", [])
            candidate_title = titles[0].strip() if titles else ""
            normalized_candidate = re.sub(
                r"[^\w\s]", "", candidate_title.lower()
            )
            normalized_candidate = " ".join(normalized_candidate.split())
            ratio = SequenceMatcher(
                None, normalized_title, normalized_candidate
            ).ratio()
            if ratio >= 0.85:
                matches.append((ratio, item))

        matches.sort(key=lambda match: match[0], reverse=True)
        if not matches:
            return None
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.03:
            return None
        return _parse_crossref_message(matches[0][1])
    except Exception:
        return None
    return None


def _parse_crossref_message(
    message: dict[str, Any],
    doi: Optional[str] = None,
) -> Optional[ExtractedMetadata]:
    titles = message.get("title", [])
    title = titles[0].strip() if titles else ""
    if not title:
        return None

    containers = message.get("container-title", [])
    creators: list[dict[str, str]] = []
    for author in message.get("author", []):
        given = author.get("given", "").strip()
        family = author.get("family", "").strip()
        name = author.get("name", "").strip()
        if family or given:
            creators.append({
                "creatorType": "author",
                "firstName": given,
                "lastName": family,
            })
        elif name:
            creators.append({"creatorType": "author", "name": name})

    date_str = None
    issued = message.get("issued", {}).get("date-parts", [[]])
    if issued and issued[0]:
        parts = [str(part) for part in issued[0] if part is not None]
        date_str = "-".join(parts)

    type_mapping = {
        "journal-article": "journalArticle",
        "proceedings-article": "conferencePaper",
        "book": "book",
        "book-chapter": "bookSection",
        "report": "report",
        "posted-content": "preprint",
    }
    isbn_values = message.get("ISBN", [])
    if isinstance(isbn_values, str):
        isbn = isbn_values
    else:
        isbn = isbn_values[0] if isbn_values else None
    return ExtractedMetadata(
        title=title,
        creators=creators,
        date=date_str,
        publication_title=containers[0].strip() if containers else None,
        doi=clean_doi(doi or message.get("DOI", "")) or None,
        isbn=isbn,
        item_type=type_mapping.get(
            message.get("type", "journal-article"), "journalArticle"
        ),
        abstract_note=message.get("abstract"),
        url=message.get("URL"),
        volume=str(message.get("volume")) if message.get("volume") else None,
        issue=str(message.get("issue")) if message.get("issue") else None,
        pages=message.get("page"),
        source="crossref",
    )


def merge_missing_metadata(
    metadata: ExtractedMetadata,
    supplement: ExtractedMetadata,
) -> ExtractedMetadata:
    """Fill empty bibliographic fields without replacing extracted values."""
    if not metadata.creators:
        metadata.creators = supplement.creators
    for field_name in (
        "date", "publication_title", "doi", "isbn", "abstract_note", "url",
        "volume", "issue", "pages",
    ):
        if not getattr(metadata, field_name):
            setattr(metadata, field_name, getattr(supplement, field_name))
    if metadata.item_type == "document":
        metadata.item_type = supplement.item_type
    if supplement.creators:
        metadata.source = "crossref_title"
    return metadata


def parse_filename_heuristics(file_path: Path) -> ExtractedMetadata:
    """Extract metadata based on path hierarchy and filename patterns."""
    stem = file_path.stem.strip()
    year = None
    publication = None

    # Check parent directory hierarchy:
    # e.g., .../<Publication>/<Year>/<Author - Title>.pdf
    parents = file_path.parents
    if len(parents) >= 1:
        parent_name = parents[0].name.strip()
        if re.match(r"^(19|20)\d{2}$", parent_name):
            year = parent_name
            if len(parents) >= 2 and parents[1].name != ".":
                publication = parents[1].name.strip()
        elif parent_name and parent_name not in (".", "Articles", "Documents", "Downloads"):
            publication = parent_name

    creators: list[dict[str, str]] = []
    title = stem

    # Pattern: "<Author(s)> - <Year> - <Title>" or "<Author(s)> - <Title>"
    if " - " in stem:
        parts = stem.split(" - ")
        if len(parts) >= 3 and re.match(r"^(19|20)\d{2}$", parts[1].strip()):
            raw_author = parts[0].strip()
            year = parts[1].strip()
            title = " - ".join(parts[2:]).strip()
        elif len(parts) >= 2:
            raw_author = parts[0].strip()
            title = " - ".join(parts[1:]).strip()
        else:
            raw_author = ""

        if raw_author and raw_author.lower() not in ("unknown", "divers", "(divers)", "anonyme"):
            # Determine author delimiter
            if any(sep in raw_author for sep in (";", "/", " and ", " et ", " & ")):
                author_chunks = re.split(r";|/|\s+(?:and|et|&)\s+", raw_author)
            elif raw_author.count(",") > 1:
                author_chunks = [a.strip() for a in raw_author.split(",")]
            else:
                author_chunks = [raw_author]

            for single_auth in author_chunks:
                single_auth = single_auth.strip()
                if not single_auth:
                    continue
                # If "LastName, FirstName"
                if "," in single_auth:
                    aparts = single_auth.split(",", 1)
                    creators.append({
                        "creatorType": "author",
                        "lastName": aparts[0].strip(),
                        "firstName": aparts[1].strip(),
                    })
                elif " " in single_auth:
                    aparts = single_auth.rsplit(" ", 1)
                    creators.append({
                        "creatorType": "author",
                        "firstName": aparts[0].strip(),
                        "lastName": aparts[1].strip(),
                    })
                else:
                    creators.append({
                        "creatorType": "author",
                        "lastName": single_auth,
                    })

    # Clean up title formatting
    title = re.sub(r"^[_\s-]+|[_\s-]+$", "", title)
    title = re.sub(r"\s+", " ", title)
    if not title:
        title = stem

    return ExtractedMetadata(
        title=title,
        creators=creators,
        date=year,
        publication_title=publication,
        item_type="journalArticle" if publication else "document",
        source="filename_heuristic",
    )


def extract_pdf_metadata(file_path: Path, use_crossref: bool = True, timeout: float = 6.0) -> ExtractedMetadata:
    """
    Extract comprehensive metadata for a PDF file with fallback cascade:
    1. Look for DOI -> CrossRef query
    2. Embedded PDF Document Info (Title, Author, CreationDate)
    3. Filename & Directory structure heuristics
    4. Fallback using filename as title
    """
    found_doi: Optional[str] = None
    pdf_meta_title: Optional[str] = None
    pdf_meta_author: Optional[str] = None
    pdf_meta_date: Optional[str] = None

    # Step 1: Attempt to read PDF with pypdf
    try:
        reader = PdfReader(str(file_path))
        # Check metadata dictionary
        meta = reader.metadata
        if meta:
            # Check for DOI in metadata fields
            for val in [getattr(meta, "subject", None), getattr(meta, "title", None), getattr(meta, "keywords", None)]:
                if val:
                    found_doi = find_doi_in_text(str(val))
                    if found_doi:
                        break

            if getattr(meta, "title", None) and str(meta.title).strip():
                t = str(meta.title).strip()
                # Ignore generic titles
                if len(t) > 3 and not re.match(r"^(untitled|microsoft word|pdf document)", t, re.I):
                    pdf_meta_title = t

            if getattr(meta, "author", None) and str(meta.author).strip():
                a = str(meta.author).strip()
                if len(a) > 2 and not re.match(r"^(user|admin|administrator|author)$", a, re.I):
                    pdf_meta_author = a

            if getattr(meta, "creation_date", None):
                d_match = re.search(r"(19|20)\d{2}", str(meta.creation_date))
                if d_match:
                    pdf_meta_date = d_match.group(0)

        # If DOI not found yet, scan the first 3 pages of text
        if not found_doi and reader.pages:
            for page in reader.pages[:3]:
                try:
                    page_text = page.extract_text() or ""
                    found_doi = find_doi_in_text(page_text)
                    if found_doi:
                        break
                except Exception:
                    continue
    except Exception:
        # Corrupted or unreadable PDF header, fallback directly to filename heuristics
        pass

    # Step 2: If DOI found and CrossRef enabled, query CrossRef
    crossref_meta = None
    if found_doi and use_crossref:
        cr_meta = fetch_crossref_metadata(found_doi, timeout=timeout)
        if cr_meta and cr_meta.creators:
            return cr_meta
        crossref_meta = cr_meta

    # Step 3: Heuristic extraction from filename and parent directories
    heuristic_meta = parse_filename_heuristics(file_path)

    # Step 4: Combine or pick best metadata
    # If we found a DOI but CrossRef was skipped or failed:
    if found_doi:
        heuristic_meta.doi = found_doi

    # If heuristic didn't find title/author, use PDF metadata if available
    if pdf_meta_title and (heuristic_meta.title == file_path.stem or len(heuristic_meta.title) < 4):
        heuristic_meta.title = pdf_meta_title
        heuristic_meta.source = "pdf_meta"

    if pdf_meta_author and not heuristic_meta.creators:
        heuristic_meta.creators = [{"creatorType": "author", "name": pdf_meta_author}]

    if pdf_meta_date and not heuristic_meta.date:
        heuristic_meta.date = pdf_meta_date

    if crossref_meta:
        merge_missing_metadata(heuristic_meta, crossref_meta)

    if use_crossref and not heuristic_meta.creators:
        title_meta = fetch_crossref_metadata_by_title(
            heuristic_meta.title,
            timeout=timeout,
        )
        if title_meta:
            merge_missing_metadata(heuristic_meta, title_meta)

    return heuristic_meta
