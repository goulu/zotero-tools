"""Cleaning rules for document titles and embedded citations."""

import html
import re
from typing import Any, Optional
from zotero_cleaner.cleaners.base import BaseCleaner, CleanIssue
from zotero_cleaner.models import ZoteroItem


def parse_embedded_authors(authors_str: str) -> list[dict[str, str]]:
    """Parse author string extracted from citation into structured creator dicts."""
    s = authors_str.strip()

    # Handle Jr. / Sr. suffixes temporarily to avoid mis-splitting
    s = re.sub(r",\s*(Jr\.?|Sr\.?|III|IV)\b", r" ##\1##", s, flags=re.IGNORECASE)

    if " and " in s:
        raw_list = s.split(" and ")
    elif ";" in s:
        raw_list = s.split(";")
    else:
        if s.count(",") == 1:
            parts = [p.strip() for p in s.split(",")]
            # If both sides look like full names (>= 2 words each), treat as 2 authors
            if len(parts[0].split()) >= 2 and len(parts[1].split()) >= 2:
                raw_list = parts
            else:
                # Single author in "Last, First" format
                first = parts[1].replace("##", "")
                last = parts[0].replace("##", "")
                return [{"lastName": last, "firstName": first, "creatorType": "author"}]
        elif s.count(",") > 1:
            raw_list = [p.strip() for p in s.split(",")]
        else:
            raw_list = [s]

    creators = []
    for part in raw_list:
        part = part.replace("##", "").strip()
        if not part:
            continue
        if "," in part:
            p = part.split(",", 1)
            creators.append({
                "lastName": p[0].strip(),
                "firstName": p[1].strip(),
                "creatorType": "author",
            })
        else:
            words = part.split()
            if len(words) == 1:
                creators.append({"lastName": words[0], "firstName": "", "creatorType": "author"})
            else:
                creators.append({
                    "lastName": words[-1],
                    "firstName": " ".join(words[:-1]),
                    "creatorType": "author",
                })
    return creators


def extract_citation_from_title(title: str) -> tuple[str, dict[str, Any], list[str]]:
    """
    Detect if the title starts with an author-year citation like:
    'Frédéric Leclerc (2018) : Un temps pour l’éternité Éd livre, 2018, ISBN: 978-2414258000.'
    Extracts author(s), year, ISBN, publisher and returns the cleaned title.
    """
    pattern = re.compile(r"^([A-ZÀ-ÖØ-ß][\w\s.,\'-]+?)\s*\((\d{4})\)\s*(?:[:.]\s*|\s+)(.+)$")
    m = pattern.match(title.strip())
    if not m:
        return title, {}, []

    authors_raw = m.group(1).strip()
    year_str = m.group(2).strip()
    rest = m.group(3).strip()

    reasons = ["Extracted author(s) and year from title"]
    metadata: dict[str, Any] = {
        "date": year_str,
        "creators": parse_embedded_authors(authors_raw),
    }

    # 1. Extract ISBN at end of title
    isbn_match = re.search(r"[,;.]?\s*ISBN(?:\s*[:-]|\s+)\s*([0-9X-]{10,17})\.?$", rest, re.IGNORECASE)
    if isbn_match:
        isbn_val = isbn_match.group(1).replace("-", "").replace(" ", "").strip()
        metadata["ISBN"] = isbn_val
        rest = rest[:isbn_match.start()].rstrip(" ,;.")
        reasons.append(f"Extracted ISBN ({isbn_val}) from title")

    # 2. Strip repeated year at end
    if year_str:
        rest = re.sub(r"[,;.]?\s*" + year_str + r"\.?$", "", rest).rstrip(" ,;.")

    # 3. Strip publisher / edition markers at end (e.g. 'Éd livre', 'Editions Odile Jacob')
    pub_match = re.search(r"[,;.]?\s*(?:Éd\.?|Ed\.?|Éditions?)\s+([^,;]+)$", rest, flags=re.IGNORECASE)
    if pub_match:
        metadata["publisher"] = pub_match.group(1).strip()
        rest = rest[:pub_match.start()].rstrip(" ,;.")
        reasons.append("Extracted publisher from title")
    else:
        # Check for trailing publishers preceded by ' , ' (e.g. ' , Routledge,' or ' , Pergamon Press...')
        pub_sep = re.search(r"\s+,\s+([A-ZÀ-ÖØ-ß][^,;]+(?:,\s*[^,;]+)?)\.?$", rest)
        if pub_sep:
            metadata["publisher"] = pub_sep.group(1).strip()
            rest = rest[:pub_sep.start()].rstrip(" ,;.")
            reasons.append("Extracted publisher from title")

    # 4. Strip lingering bibtex or arXiv artifacts
    rest = re.sub(r"\.?\s*arXiv(?:\.[a-z0-9-]+)?(?:\s*\{\{\}\})?", "", rest, flags=re.IGNORECASE).rstrip(" ,;.")
    rest = re.sub(r"\{\{\}\}", "", rest).strip(" ,;.")

    return rest, metadata, reasons


class TitleCleaner(BaseCleaner):
    def __init__(
        self,
        fix_all_caps: bool = True,
        strip_trailing_dot: bool = True,
        extract_citations: bool = True,
    ):
        self.fix_all_caps = fix_all_caps
        self.strip_trailing_dot = strip_trailing_dot
        self.extract_citations = extract_citations

    def clean_string(self, title: str) -> tuple[str, list[str]]:
        """Clean title string and return transformed title along with reasons."""
        reasons = []
        new_title = title

        # 1. Decode HTML entities (e.g. &amp; -> &)
        decoded = html.unescape(new_title)
        if decoded != new_title:
            reasons.append("Decoded HTML entities")
            new_title = decoded

        # 2. Strip HTML tags (e.g. <i>, </i>, <b>, <jats:...>)
        without_html = re.sub(r"<[^>]+>", "", new_title)
        if without_html != new_title:
            reasons.append("Removed HTML tags")
            new_title = without_html

        # 3. Strip enclosing BibTeX brackets ({My Title} -> My Title)
        if new_title.startswith("{") and new_title.endswith("}"):
            new_title = new_title[1:-1]
            reasons.append("Removed enclosing BibTeX braces")

        # 4. Normalize whitespace (newlines, tabs, multiple consecutive spaces)
        normalized_spaces = " ".join(new_title.split())
        if normalized_spaces != new_title:
            reasons.append("Normalized whitespace & line breaks")
            new_title = normalized_spaces

        # 5. ALL-CAPS detection
        letters = [c for c in new_title if c.isalpha()]
        words = new_title.split()
        if self.fix_all_caps and len(words) >= 2 and len(letters) >= 8:
            if all(c.isupper() for c in letters):
                reasons.append("Converted ALL-CAPS to Sentence case")
                new_title = new_title[0].upper() + new_title[1:].lower()

        # 6. Unnecessary trailing dot removal
        if self.strip_trailing_dot and new_title.endswith(".") and not new_title.endswith(".."):
            is_initial = bool(re.search(r"(?:^|[\s,;:(])([A-Za-z])\.$", new_title))
            is_abbr = any(new_title.lower().endswith(abbr) for abbr in ["etc.", "al.", "inc.", "co.", "jr.", "sr.", "ed.", "vol."])
            if not is_initial and not is_abbr:
                new_title = new_title[:-1].rstrip()
                reasons.append("Removed trailing period")

        return new_title, reasons

    def analyze(self, item: ZoteroItem) -> list[CleanIssue]:
        title = item.title
        if not title:
            return []

        working_title = title
        accumulated_reasons: list[str] = []
        patch_data: dict[str, Any] = {}

        # First pass: Extract embedded citation if present (Author (Year) : Title ...)
        if self.extract_citations:
            clean_t, metadata, cit_reasons = extract_citation_from_title(working_title)
            if cit_reasons:
                working_title = clean_t
                accumulated_reasons.extend(cit_reasons)

                # Populate creators if item currently has none
                if metadata.get("creators") and not item.data.creators:
                    patch_data["creators"] = metadata["creators"]

                # Populate date if item currently has none
                if metadata.get("date") and not item.data.date:
                    patch_data["date"] = metadata["date"]

                # Populate ISBN if item currently has none
                if metadata.get("ISBN") and not item.data.ISBN:
                    patch_data["ISBN"] = metadata["ISBN"]

        # Second pass: Standard string cleanup on remaining title
        cleaned_title, format_reasons = self.clean_string(working_title)
        accumulated_reasons.extend(format_reasons)

        if cleaned_title != title or patch_data:
            patch_data["title"] = cleaned_title
            return [
                CleanIssue(
                    item_key=item.key,
                    item_version=item.version,
                    item_title=item.title,
                    issue_type="title_and_citation_formatting",
                    description="; ".join(accumulated_reasons),
                    original_value=title,
                    proposed_value=cleaned_title,
                    patch_data=patch_data,
                    can_auto_fix=True,
                )
            ]
        return []
