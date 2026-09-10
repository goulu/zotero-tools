"""Detection of duplicate references in a Zotero library."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from zotero_cleaner.models import ZoteroItem


def normalize_string(text: str) -> str:
    """Remove accents, punctuation, and convert to lowercase."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


def extract_year(date_str: Optional[str]) -> Optional[str]:
    """Extract a 4-digit year from a date string."""
    if not date_str:
        return None
    match = re.search(r"\b(19\d{2}|20\d{2})\b", date_str)
    return match.group(1) if match else None


def calculate_item_richness(item: ZoteroItem) -> int:
    """Calculate a score reflecting metadata completeness."""
    score = 0
    data = item.data
    if data.DOI:
        score += 15
    if data.ISBN:
        score += 10
    if data.url:
        score += 5
    if data.abstractNote and len(data.abstractNote) > 20:
        score += 10
    if data.creators:
        score += len(data.creators) * 3
    if data.date:
        score += 5
    if data.publicationTitle:
        score += 5
    if item.meta.get("numChildren", 0) > 0:
        score += item.meta.get("numChildren", 0) * 10
    return score


@dataclass
class DuplicateGroup:
    criterion: str  # "DOI", "ISBN", "Title + Year + Author"
    key_value: str
    items: list[ZoteroItem] = field(default_factory=list)
    master_item: Optional[ZoteroItem] = None

    def determine_master(self):
        """Pick the most complete item as master reference."""
        if not self.items:
            return
        self.items.sort(
            key=lambda it: (calculate_item_richness(it), it.data.key),
            reverse=True,
        )
        self.master_item = self.items[0]

    @property
    def duplicate_items(self) -> list[ZoteroItem]:
        if not self.master_item:
            self.determine_master()
        return [it for it in self.items if it.key != (self.master_item.key if self.master_item else "")]


class DuplicateDetector:
    def __init__(self):
        pass

    def find_duplicates(self, items: list[ZoteroItem]) -> list[DuplicateGroup]:
        """Detect duplicate groups among a list of references."""
        doi_map: dict[str, list[ZoteroItem]] = defaultdict(list)
        isbn_map: dict[str, list[ZoteroItem]] = defaultdict(list)
        title_author_map: dict[str, list[ZoteroItem]] = defaultdict(list)

        for item in items:
            if not item.is_top_level_reference:
                continue

            # 1. DOI
            doi = item.doi
            if doi and len(doi) > 5:
                doi_map[doi].append(item)

            # 2. ISBN
            isbn = item.isbn
            if isbn and len(isbn) >= 10:
                isbn_map[isbn].append(item)

            # 3. Normalized Title + Year + First Author
            raw_title = item.title
            if raw_title and len(raw_title) >= 10:
                norm_title = normalize_string(raw_title)
                if len(norm_title) >= 10:
                    year = extract_year(item.data.date) or "XXXX"
                    first_author = ""
                    if item.data.creators:
                        c = item.data.creators[0]
                        first_author = normalize_string(c.lastName or c.name or "")
                    key_val = f"{norm_title}::{year}::{first_author}"
                    title_author_map[key_val].append(item)

        groups: list[DuplicateGroup] = []
        already_grouped_keys: set[str] = set()

        # Groups by DOI
        for doi, group_items in doi_map.items():
            if len(group_items) > 1:
                grp = DuplicateGroup(criterion="DOI", key_value=doi, items=group_items)
                grp.determine_master()
                groups.append(grp)
                for it in group_items:
                    already_grouped_keys.add(it.key)

        # Groups by ISBN
        for isbn, group_items in isbn_map.items():
            remaining = [it for it in group_items if it.key not in already_grouped_keys]
            if len(group_items) > 1 and len(remaining) > 0:
                grp = DuplicateGroup(criterion="ISBN", key_value=isbn, items=group_items)
                grp.determine_master()
                groups.append(grp)
                for it in group_items:
                    already_grouped_keys.add(it.key)

        # Groups by Title + Year + Author
        for key_val, group_items in title_author_map.items():
            unassigned = [it for it in group_items if it.key not in already_grouped_keys]
            if len(group_items) > 1 and len(unassigned) >= 2:
                grp = DuplicateGroup(
                    criterion="Title + Year + Author",
                    key_value=key_val.split("::")[0][:30] + "...",
                    items=group_items,
                )
                grp.determine_master()
                groups.append(grp)
                for it in group_items:
                    already_grouped_keys.add(it.key)

        return groups
