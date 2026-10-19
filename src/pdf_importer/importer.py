"""PDF Import Engine for Zotero."""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from zotero_cleaner.client import ZoteroClient
from pdf_importer.config import PDFImporterConfig, DEFAULT_IMPORTER_CONFIG
from pdf_importer.extractor import (
    ExtractedMetadata,
    compute_file_md5,
    extract_pdf_metadata,
)
from pdf_importer.indexer import ZoteroLibraryIndex


@dataclass
class ItemImportRecord:
    file_path: Path
    status: str  # 'imported', 'skipped_duplicate', 'error', 'dry_run_import'
    reason: Optional[str] = None
    item_key: Optional[str] = None
    metadata: Optional[ExtractedMetadata] = None


@dataclass
class ImportSummary:
    total_scanned: int = 0
    imported: int = 0
    duplicates_skipped: int = 0
    errors: int = 0
    records: list[ItemImportRecord] = field(default_factory=list)


def discover_pdf_files(
    target_path: Path,
    recursive: bool = True,
    max_files: Optional[int] = None,
) -> Generator[Path, None, None]:
    """
    Stream PDF files from a directory or single file efficiently.
    Uses os.walk for fast filesystem traversal over network shares.
    """
    if target_path.is_file():
        if target_path.name.lower().endswith(".pdf"):
            yield target_path
        return

    count = 0
    if recursive:
        for dirpath, _, filenames in os.walk(str(target_path)):
            for fname in sorted(filenames):
                if fname.lower().endswith(".pdf"):
                    yield Path(dirpath) / fname
                    count += 1
                    if max_files and count >= max_files:
                        return
    else:
        for p in sorted(target_path.iterdir()):
            if p.is_file() and p.name.lower().endswith(".pdf"):
                yield p
                count += 1
                if max_files and count >= max_files:
                    return


class PDFImporter:
    def __init__(
        self,
        client: Optional[ZoteroClient] = None,
        config: Optional[PDFImporterConfig] = None,
        index: Optional[ZoteroLibraryIndex] = None,
    ):
        self.client = client or ZoteroClient()
        self.config = config or DEFAULT_IMPORTER_CONFIG
        self.index = index or ZoteroLibraryIndex()
        self.index_loaded = False

    def ensure_index(
        self,
        include_references: bool = True,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ):
        """Build library index if not already populated."""
        if not self.index_loaded:
            self.index.load_library(
                self.client,
                include_references=include_references,
                on_progress=on_progress,
            )
            self.index_loaded = True

    def import_single_pdf(
        self,
        file_path: Path,
        dry_run: bool = False,
        use_crossref: bool = True,
        compute_md5: bool = True,
    ) -> ItemImportRecord:
        """
        Process a single PDF: check duplicates, extract metadata, and import if new.
        """
        if not file_path.exists():
            return ItemImportRecord(file_path=file_path, status="error", reason="File does not exist")

        # 1. Quick duplicate check (filename & path)
        is_dup, reason = self.index.check_duplicate(file_path)
        if is_dup:
            return ItemImportRecord(file_path=file_path, status="skipped_duplicate", reason=reason)

        # 2. Compute MD5 checksum and verify duplicate
        md5_hash = None
        if compute_md5:
            try:
                md5_hash = compute_file_md5(file_path)
                is_dup, reason = self.index.check_duplicate(file_path, md5_hash=md5_hash)
                if is_dup:
                    return ItemImportRecord(
                        file_path=file_path,
                        status="skipped_duplicate",
                        reason=reason,
                    )
            except Exception as e:
                return ItemImportRecord(
                    file_path=file_path,
                    status="error",
                    reason=f"Failed to read file for MD5: {e}",
                )

        # 3. Extract metadata
        try:
            metadata = extract_pdf_metadata(
                file_path,
                use_crossref=use_crossref,
                timeout=self.config.crossref_timeout,
            )
        except Exception as e:
            metadata = ExtractedMetadata(title=file_path.stem, source="fallback")

        # 4. Deep duplicate check (DOI and Title + Author)
        is_dup, reason = self.index.check_duplicate(file_path, md5_hash=md5_hash, metadata=metadata)
        if is_dup:
            return ItemImportRecord(
                file_path=file_path,
                status="skipped_duplicate",
                reason=reason,
                metadata=metadata,
            )

        # 5. If dry-run, do not alter Zotero
        if dry_run:
            return ItemImportRecord(
                file_path=file_path,
                status="dry_run_import",
                reason="Ready to import (new reference)",
                metadata=metadata,
            )

        # 6. Execute import into Zotero
        try:
            parent_key, child_key = self._create_zotero_entry(file_path, metadata, md5_hash)
            # Update in-memory index
            self.index.record_imported(file_path, md5_hash=md5_hash, metadata=metadata)

            return ItemImportRecord(
                file_path=file_path,
                status="imported",
                item_key=parent_key,
                reason=f"Successfully imported (Parent: {parent_key}, Child: {child_key})",
                metadata=metadata,
            )
        except Exception as e:
            return ItemImportRecord(
                file_path=file_path,
                status="error",
                reason=f"Failed to create item in Zotero: {e}",
                metadata=metadata,
            )

    def _create_zotero_entry(
        self,
        file_path: Path,
        metadata: ExtractedMetadata,
        md5_hash: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Create parent item + attached PDF file in Zotero.
        Returns (parent_key, child_key).
        """
        # Step A: Create parent item
        parent_payload = [metadata.to_zotero_item_payload()]
        res_parent = self.client.create_items(parent_payload)
        successful = res_parent.get("successful", {})
        if not successful:
            failed = res_parent.get("failed", {})
            raise RuntimeError(f"Parent item creation failed: {failed}")

        first_key = next(iter(successful.keys()))
        parent_item_info = successful[first_key]
        parent_key = parent_item_info.get("key")
        parent_version = parent_item_info.get("version")

        # Step B: Create child attachment
        link_mode = self.config.link_mode
        if link_mode == "linked":
            child_payload = [{
                "itemType": "attachment",
                "parentItem": parent_key,
                "linkMode": "linked_file",
                "title": file_path.name,
                "path": str(file_path.resolve()),
                "contentType": "application/pdf",
            }]
        else:  # imported_file
            child_payload = [{
                "itemType": "attachment",
                "parentItem": parent_key,
                "linkMode": "imported_file",
                "title": file_path.name,
                "filename": file_path.name,
                "contentType": "application/pdf",
            }]

        res_child = self.client.create_items(child_payload, current_version=parent_version)
        child_success = res_child.get("successful", {})
        if not child_success:
            # Attempt to roll back parent
            try:
                self.client.delete_item(parent_key, parent_version)
            except Exception:
                pass
            failed = res_child.get("failed", {})
            raise RuntimeError(f"Attachment item creation failed: {failed}")

        c_first_key = next(iter(child_success.keys()))
        child_item_info = child_success[c_first_key]
        child_key = child_item_info.get("key")

        # If imported_file, copy file into Zotero storage folder
        if link_mode == "imported":
            storage_item_dir = self.config.storage_dir / child_key
            storage_item_dir.mkdir(parents=True, exist_ok=True)
            target_file = storage_item_dir / file_path.name
            shutil.copy2(file_path, target_file)

        return parent_key, child_key

    def import_directory(
        self,
        directory: Path,
        recursive: bool = True,
        dry_run: bool = False,
        max_files: Optional[int] = None,
        use_crossref: bool = True,
        on_file_processed: Optional[Callable[[ItemImportRecord, int], None]] = None,
    ) -> ImportSummary:
        """
        Scan and process all PDFs in target directory.
        """
        summary = ImportSummary()
        processed_count = 0

        for pdf_path in discover_pdf_files(directory, recursive=recursive, max_files=max_files):
            processed_count += 1
            summary.total_scanned += 1

            record = self.import_single_pdf(
                pdf_path,
                dry_run=dry_run,
                use_crossref=use_crossref,
            )

            if record.status in ("imported", "dry_run_import"):
                summary.imported += 1
            elif record.status == "skipped_duplicate":
                summary.duplicates_skipped += 1
            elif record.status == "error":
                summary.errors += 1

            summary.records.append(record)

            if on_file_processed:
                on_file_processed(record, processed_count)

        return summary
