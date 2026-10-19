"""Zotero library indexer for high-speed duplicate detection."""

import os
import re
from pathlib import Path
from typing import Callable, Optional
from zotero_cleaner.client import ZoteroClient
from zotero_cleaner.models import ZoteroItem
from pdf_importer.extractor import ExtractedMetadata, clean_doi


def normalize_title(title: str) -> str:
    """Normalize title for matching (lowercase, alphanumeric only)."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())


class ZoteroLibraryIndex:
    """
    Maintains an in-memory cache of the existing Zotero library to rapidly
    determine if a PDF or reference is already present.
    """

    def __init__(self):
        self.filenames: set[str] = set()
        self.paths: set[str] = set()
        self.md5_hashes: set[str] = set()
        self.dois: set[str] = set()
        self.titles: set[str] = set()
        self.title_author_pairs: set[tuple[str, str]] = set()
        self.references_by_doi: dict[str, ZoteroItem] = {}
        self.references_by_title: dict[str, ZoteroItem] = {}

    @property
    def total_attachments(self) -> int:
        return len(self.filenames)

    @property
    def total_dois(self) -> int:
        return len(self.dois)

    def load_library(
        self,
        client: ZoteroClient,
        include_references: bool = True,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ):
        """
        Populate the duplicate detection index from the running Zotero instance.
        """
        # Phase 1: Load all attachments (filenames, paths, MD5s)
        total_atts = client.get_total_items(top_level=False, item_type="attachment")
        loaded_atts = 0
        if on_progress:
            on_progress("attachments", 0, total_atts)

        for item in client.iter_all_items(batch_size=100, top_level=False, item_type="attachment"):
            self._index_attachment(item)
            loaded_atts += 1
            if on_progress and loaded_atts % 100 == 0:
                on_progress("attachments", loaded_atts, total_atts)

        if on_progress:
            on_progress("attachments", loaded_atts, total_atts)

        # Phase 2: Load top-level bibliographic items (DOIs, Titles, Creators)
        if include_references:
            total_refs = client.get_total_items(top_level=True)
            loaded_refs = 0
            if on_progress:
                on_progress("references", 0, total_refs)

            for item in client.iter_all_items(batch_size=100, top_level=True):
                self._index_reference(item)
                loaded_refs += 1
                if on_progress and loaded_refs % 200 == 0:
                    on_progress("references", loaded_refs, total_refs)

            if on_progress:
                on_progress("references", loaded_refs, total_refs)

    def _index_attachment(self, item: ZoteroItem):
        data = item.data
        if data.filename:
            self.filenames.add(data.filename.lower())
        if data.title and data.title.lower().endswith(".pdf"):
            self.filenames.add(data.title.lower())
        if data.path:
            self.paths.add(os.path.normpath(data.path).lower())
        if data.md5:
            self.md5_hashes.add(data.md5.lower())

    def _index_reference(self, item: ZoteroItem):
        if item.doi:
            normalized_doi = clean_doi(item.doi).lower()
            self.dois.add(normalized_doi)
            self.references_by_doi[normalized_doi] = item
        if item.title:
            norm_t = normalize_title(item.title)
            if len(norm_t) > 5:
                self.titles.add(norm_t)
                self.references_by_title[norm_t] = item

                # Author last name pair
                author_last = ""
                for c in item.data.creators:
                    if c.lastName:
                        author_last = c.lastName.lower().strip()
                        break
                    elif c.name:
                        author_last = c.name.lower().strip()
                        break
                if author_last:
                    self.title_author_pairs.add((norm_t, author_last))

    def find_reference(self, metadata: ExtractedMetadata) -> Optional[ZoteroItem]:
        """Find an indexed reference by DOI or exact normalized title."""
        if metadata.doi:
            item = self.references_by_doi.get(clean_doi(metadata.doi).lower())
            if item:
                return item
        if metadata.title:
            return self.references_by_title.get(normalize_title(metadata.title))
        return None

    def check_duplicate(
        self,
        file_path: Path,
        md5_hash: Optional[str] = None,
        metadata: Optional[ExtractedMetadata] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a file or its metadata matches an existing Zotero record.
        Returns (is_duplicate, reason_string).
        """
        filename_lower = file_path.name.lower()
        if filename_lower in self.filenames:
            return True, f"Filename already exists in Zotero ('{file_path.name}')"

        path_norm = os.path.normpath(str(file_path.resolve())).lower()
        if path_norm in self.paths:
            return True, f"File path already linked in Zotero ('{file_path}')"

        if md5_hash and md5_hash.lower() in self.md5_hashes:
            return True, f"MD5 checksum matched existing attachment ({md5_hash})"

        if metadata:
            if metadata.doi:
                cleaned_doi = clean_doi(metadata.doi).lower()
                if cleaned_doi in self.dois:
                    return True, f"DOI already in library ({metadata.doi})"

            if metadata.title:
                norm_t = normalize_title(metadata.title)
                matching_reference = self.references_by_title.get(norm_t)
                if matching_reference and not matching_reference.data.creators:
                    return True, f"Title matched existing item without an author ('{metadata.title}')"
                author_last = ""
                if metadata.creators:
                    c0 = metadata.creators[0]
                    author_last = (c0.get("lastName") or c0.get("name") or "").lower().strip()

                if author_last and (norm_t, author_last) in self.title_author_pairs:
                    return True, f"Title & author matched existing item ('{metadata.title}' by {author_last})"

        return False, None

    def record_imported(
        self,
        file_path: Path,
        md5_hash: Optional[str] = None,
        metadata: Optional[ExtractedMetadata] = None,
    ):
        """Register newly created item in the local index to prevent duplicates within same run."""
        self.filenames.add(file_path.name.lower())
        self.paths.add(os.path.normpath(str(file_path.resolve())).lower())
        if md5_hash:
            self.md5_hashes.add(md5_hash.lower())
        if metadata:
            if metadata.doi:
                self.dois.add(clean_doi(metadata.doi).lower())
            if metadata.title:
                norm_t = normalize_title(metadata.title)
                self.titles.add(norm_t)
                author_last = ""
                if metadata.creators:
                    c0 = metadata.creators[0]
                    author_last = (c0.get("lastName") or c0.get("name") or "").lower().strip()
                if author_last:
                    self.title_author_pairs.add((norm_t, author_last))
