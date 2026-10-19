"""PDF Import Engine for Zotero."""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from zotero_cleaner.client import ZoteroClient
from zotero_cleaner.models import ZoteroItem
from pdf_importer.config import PDFImporterConfig, DEFAULT_IMPORTER_CONFIG
from pdf_importer.extractor import (
    ExtractedMetadata,
    apply_path_metadata_rules,
    compute_file_md5,
    extract_pdf_metadata,
    fetch_crossref_metadata_by_title,
    merge_missing_metadata,
    parse_filename_heuristics,
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
        extracted_metadata: Optional[ExtractedMetadata] = None,
    ) -> ItemImportRecord:
        """
        Process a single PDF: check duplicates, extract metadata, and import if new.
        """
        if not file_path.exists():
            return ItemImportRecord(file_path=file_path, status="error", reason="File does not exist")

        # 1. Quick duplicate check (filename & path)
        is_dup, reason = self.index.check_duplicate(file_path)
        quick_duplicate_reason = reason if is_dup else None

        # 2. Compute MD5 checksum and verify duplicate
        md5_hash = None
        if compute_md5:
            try:
                md5_hash = compute_file_md5(file_path)
                is_dup, reason = self.index.check_duplicate(file_path, md5_hash=md5_hash)
                if is_dup and not quick_duplicate_reason:
                    quick_duplicate_reason = reason
            except Exception as e:
                return ItemImportRecord(
                    file_path=file_path,
                    status="error",
                    reason=f"Failed to read file for MD5: {e}",
                )

        # 3. Extract metadata
        if extracted_metadata is None:
            try:
                metadata = extract_pdf_metadata(
                    file_path,
                    use_crossref=use_crossref,
                    timeout=self.config.crossref_timeout,
                )
            except Exception:
                metadata = ExtractedMetadata(
                    title=file_path.stem,
                    source="fallback",
                )
        else:
            metadata = extracted_metadata

        reference = self.index.find_reference(metadata)
        enrichment_preview = False
        if reference and not reference.data.creators and use_crossref:
            supplement = None
            if metadata.source == "crossref_title" and metadata.creators:
                supplement = metadata
            else:
                supplement = fetch_crossref_metadata_by_title(
                    reference.title or metadata.title,
                    timeout=self.config.crossref_timeout,
                )
            if supplement:
                merge_missing_metadata(metadata, supplement)
                patch_data = self._missing_reference_fields(
                    reference,
                    supplement,
                )
                if patch_data and dry_run:
                    enrichment_preview = True
                elif patch_data:
                    try:
                        self.client.update_item_data(
                            reference.key,
                            reference.version,
                            patch_data,
                        )
                    except Exception as e:
                        return ItemImportRecord(
                            file_path=file_path,
                            status="error",
                            reason=(
                                f"Failed to enrich Zotero item {reference.key}: {e}"
                            ),
                            metadata=metadata,
                        )

        # 4. Deep duplicate check (DOI and Title + Author)
        is_dup, reason = self.index.check_duplicate(file_path, md5_hash=md5_hash, metadata=metadata)
        if quick_duplicate_reason:
            is_dup, reason = True, quick_duplicate_reason
        if is_dup:
            if enrichment_preview:
                reason = f"{reason}; Zotero metadata would be enriched"
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

    @staticmethod
    def _missing_reference_fields(
        reference: ZoteroItem,
        metadata: ExtractedMetadata,
    ) -> dict[str, Any]:
        data = reference.data
        patch: dict[str, Any] = {}
        if not data.creators and metadata.creators:
            patch["creators"] = metadata.creators
        field_map = {
            "date": "date",
            "publication_title": "publicationTitle",
            "doi": "DOI",
            "isbn": "ISBN",
            "abstract_note": "abstractNote",
            "url": "url",
            "volume": "volume",
            "issue": "issue",
            "pages": "pages",
        }
        for metadata_field, zotero_field in field_map.items():
            value = getattr(metadata, metadata_field)
            if value and not getattr(data, zotero_field, None):
                patch[zotero_field] = value
        return patch

    @staticmethod
    def _filename_author(
        file_path: Path,
    ) -> tuple[tuple[str, str], dict[str, str]] | None:
        path_metadata = apply_path_metadata_rules(
            ExtractedMetadata(title=file_path.stem),
            file_path,
        )
        creators = (
            path_metadata.creators
            or parse_filename_heuristics(file_path).creators
        )
        if not creators:
            return None
        creator = creators[0]
        first_name = creator.get("firstName", "").strip()
        last_name = creator.get("lastName", "").strip()
        name = creator.get("name", "").strip()
        identity = (last_name.casefold(), first_name.casefold())
        if not any(identity) and name:
            identity = (name.casefold(), "")
        if not any(identity):
            return None
        return identity, creator

    def _extract_metadata_or_book_fallback(
        self,
        file_path: Path,
        use_crossref: bool,
        allow_book_fallback: bool,
    ) -> tuple[
        ExtractedMetadata,
        Optional[tuple[tuple[Path, tuple[str, str]], ExtractedMetadata]],
    ]:
        try:
            metadata = extract_pdf_metadata(
                file_path,
                use_crossref=use_crossref,
                timeout=self.config.crossref_timeout,
            )
        except Exception:
            metadata = ExtractedMetadata(
                title=file_path.stem,
                source="fallback",
            )

        if metadata.path_rule:
            return metadata, None

        if (
            metadata.source in ("crossref", "crossref_title")
            and metadata.creators
        ):
            return metadata, None

        filename_author = self._filename_author(file_path)
        search_author = None
        if metadata.creators:
            creator = metadata.creators[0]
            search_author = creator.get("name") or " ".join(filter(None, (
                creator.get("firstName", "").strip(),
                creator.get("lastName", "").strip(),
            )))
        if not search_author and filename_author:
            creator = filename_author[1]
            search_author = creator.get("name") or " ".join(filter(None, (
                creator.get("firstName", "").strip(),
                creator.get("lastName", "").strip(),
            )))

        if use_crossref:
            match = fetch_crossref_metadata_by_title(
                metadata.title,
                timeout=self.config.crossref_timeout,
                author=search_author,
            )
            if match:
                merge_missing_metadata(metadata, match)
                return metadata, None

        if not allow_book_fallback or not filename_author:
            return metadata, None

        identity, creator = filename_author
        book_directory = file_path.parent
        year = None
        if re.fullmatch(r"(?:19|20)\d{2}", book_directory.name):
            year = book_directory.name
            book_directory = book_directory.parent
        book_metadata = ExtractedMetadata(
            title=book_directory.name,
            creators=[creator],
            date=year,
            item_type="book",
            source="filename_group",
        )
        group_key = (file_path.parent.resolve(), identity)
        return metadata, (group_key, book_metadata)

    def _create_zotero_book_entry(
        self,
        file_paths: list[Path],
        metadata: ExtractedMetadata,
        md5_hashes: dict[Path, str],
        existing_reference: Optional[ZoteroItem] = None,
    ) -> tuple[str, dict[Path, str], dict[Path, str]]:
        parent_key = existing_reference.key if existing_reference else None
        parent_version = (
            existing_reference.version if existing_reference else None
        )
        created_parent = False

        if not existing_reference:
            response = self.client.create_items(
                [metadata.to_zotero_item_payload()]
            )
            successful = response.get("successful", {})
            if not successful:
                raise RuntimeError(
                    f"Book item creation failed: {response.get('failed', {})}"
                )
            parent_info = next(iter(successful.values()))
            parent_key = parent_info.get("key")
            parent_version = parent_info.get("version")
            created_parent = True

        attachment_payloads = []
        for file_path in file_paths:
            if self.config.link_mode == "linked":
                attachment_payloads.append({
                    "itemType": "attachment",
                    "parentItem": parent_key,
                    "linkMode": "linked_file",
                    "title": file_path.name,
                    "path": str(file_path.resolve()),
                    "contentType": "application/pdf",
                })
            else:
                attachment_payloads.append({
                    "itemType": "attachment",
                    "parentItem": parent_key,
                    "linkMode": "imported_file",
                    "title": file_path.name,
                    "filename": file_path.name,
                    "contentType": "application/pdf",
                })

        attached: dict[Path, str] = {}
        errors: dict[Path, str] = {}
        try:
            for start in range(0, len(file_paths), 50):
                batch_paths = file_paths[start:start + 50]
                batch_payloads = attachment_payloads[start:start + 50]
                response = self.client.create_items(
                    batch_payloads,
                    current_version=parent_version,
                )
                successful = response.get("successful", {})
                failed = response.get("failed", {})
                for offset, file_path in enumerate(batch_paths):
                    item_info = successful.get(str(offset))
                    if not item_info:
                        errors[file_path] = str(
                            failed.get(
                                str(offset),
                                "Attachment creation failed",
                            )
                        )
                        continue
                    child_key = item_info.get("key")
                    if not child_key:
                        errors[file_path] = (
                            "Zotero did not return an attachment key"
                        )
                        continue
                    attached[file_path] = child_key
                    if self.config.link_mode == "imported":
                        storage_item_dir = self.config.storage_dir / child_key
                        storage_item_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(
                            file_path,
                            storage_item_dir / file_path.name,
                        )
        except Exception:
            if created_parent and not attached:
                try:
                    self.client.delete_item(parent_key, parent_version)
                except Exception:
                    pass
            raise

        if created_parent and not attached:
            try:
                self.client.delete_item(parent_key, parent_version)
            except Exception:
                pass
        return parent_key, attached, errors

    def _import_book_group(
        self,
        file_paths: list[Path],
        metadata: ExtractedMetadata,
        dry_run: bool,
        existing_reference: Optional[ZoteroItem] = None,
        attach_to_existing: bool = False,
    ) -> list[ItemImportRecord]:
        records: list[ItemImportRecord] = []
        pending_paths: list[Path] = []
        md5_hashes: dict[Path, str] = {}

        for file_path in file_paths:
            is_duplicate, reason = self.index.check_duplicate(file_path)
            if is_duplicate:
                records.append(ItemImportRecord(
                    file_path=file_path,
                    status="skipped_duplicate",
                    reason=reason,
                    metadata=metadata,
                ))
                continue
            try:
                md5_hash = compute_file_md5(file_path)
            except Exception as error:
                records.append(ItemImportRecord(
                    file_path=file_path,
                    status="error",
                    reason=f"Failed to read file for MD5: {error}",
                    metadata=metadata,
                ))
                continue
            is_duplicate, reason = self.index.check_duplicate(
                file_path,
                md5_hash=md5_hash,
            )
            if is_duplicate:
                records.append(ItemImportRecord(
                    file_path=file_path,
                    status="skipped_duplicate",
                    reason=reason,
                    metadata=metadata,
                ))
                continue
            pending_paths.append(file_path)
            md5_hashes[file_path] = md5_hash

        if not pending_paths:
            return records

        if existing_reference is None:
            existing_reference = self.index.find_reference(metadata)
        if existing_reference and existing_reference.data.itemType != "book":
            for file_path in pending_paths:
                records.append(ItemImportRecord(
                    file_path=file_path,
                    status="skipped_duplicate",
                    reason="A matching Zotero item exists but is not a book",
                    metadata=metadata,
                ))
            return records

        if existing_reference:
            patch_data = self._missing_reference_fields(
                existing_reference,
                metadata,
            )
            if patch_data and not dry_run:
                try:
                    self.client.update_item_data(
                        existing_reference.key,
                        existing_reference.version,
                        patch_data,
                    )
                except Exception as error:
                    return records + [ItemImportRecord(
                        file_path=file_path,
                        status="error",
                        reason=f"Failed to enrich Zotero book: {error}",
                        metadata=metadata,
                    ) for file_path in pending_paths]

        if dry_run:
            action = (
                "Would attach to existing book"
                if existing_reference or attach_to_existing
                else "Would create book and attach"
            )
            return records + [ItemImportRecord(
                file_path=file_path,
                status="dry_run_import",
                reason=f"{action}: {metadata.title}",
                metadata=metadata,
            ) for file_path in pending_paths]

        try:
            parent_key, attached, errors = self._create_zotero_book_entry(
                pending_paths,
                metadata,
                md5_hashes,
                existing_reference=existing_reference,
            )
        except Exception as error:
            return records + [ItemImportRecord(
                file_path=file_path,
                status="error",
                reason=f"Failed to create Zotero book: {error}",
                metadata=metadata,
            ) for file_path in pending_paths]

        for file_path in pending_paths:
            if file_path in errors:
                records.append(ItemImportRecord(
                    file_path=file_path,
                    status="error",
                    reason=errors[file_path],
                    metadata=metadata,
                ))
                continue
            if file_path not in attached:
                continue
            self.index.record_imported(
                file_path,
                md5_hash=md5_hashes[file_path],
                metadata=metadata,
            )
            records.append(ItemImportRecord(
                file_path=file_path,
                status="imported",
                item_key=parent_key,
                reason=f"Attached to Zotero book {parent_key}",
                metadata=metadata,
            ))
        return records

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
        pdf_files = list(
            discover_pdf_files(
                directory,
                recursive=recursive,
                max_files=max_files,
            )
        )
        author_counts: dict[tuple[Path, tuple[str, str]], int] = {}
        for pdf_path in pdf_files:
            author = self._filename_author(pdf_path)
            if not author:
                continue
            identity, _ = author
            group_key = (pdf_path.parent.resolve(), identity)
            author_counts[group_key] = author_counts.get(group_key, 0) + 1

        book_states: dict[
            tuple[Path, tuple[str, str]],
            tuple[ExtractedMetadata, Optional[str]],
        ] = {}

        def record_processed(record: ItemImportRecord):
            processed_count = summary.total_scanned + 1
            summary.total_scanned += 1
            if record.status in ("imported", "dry_run_import"):
                summary.imported += 1
            elif record.status == "skipped_duplicate":
                summary.duplicates_skipped += 1
            elif record.status == "error":
                summary.errors += 1
            summary.records.append(record)
            if on_file_processed:
                on_file_processed(record, processed_count)

        for pdf_path in pdf_files:
            filename_author = self._filename_author(pdf_path)
            allow_book_fallback = False
            if filename_author:
                identity, _ = filename_author
                group_key = (pdf_path.parent.resolve(), identity)
                allow_book_fallback = author_counts.get(group_key, 0) > 1
            metadata, book_fallback = self._extract_metadata_or_book_fallback(
                pdf_path,
                use_crossref,
                allow_book_fallback=allow_book_fallback,
            )
            if book_fallback is None:
                record = self.import_single_pdf(
                    pdf_path,
                    dry_run=dry_run,
                    use_crossref=use_crossref,
                    extracted_metadata=metadata,
                )
                record_processed(record)
                continue

            book_key, book_metadata = book_fallback
            state = book_states.get(book_key)
            if state is None:
                records = self._import_book_group(
                    [pdf_path],
                    book_metadata,
                    dry_run,
                )
                for record in records:
                    record_processed(record)
                    if record.status in ("imported", "dry_run_import"):
                        book_states[book_key] = (
                            book_metadata,
                            record.item_key,
                        )
                continue

            book_metadata, parent_key = state
            existing_reference = None
            if parent_key and not dry_run:
                try:
                    existing_reference = self.client.get_item(parent_key)
                except Exception as error:
                    record_processed(ItemImportRecord(
                        file_path=pdf_path,
                        status="error",
                        reason=(
                            f"Failed to load Zotero book {parent_key}: {error}"
                        ),
                        metadata=book_metadata,
                    ))
                    continue

            records = self._import_book_group(
                [pdf_path],
                book_metadata,
                dry_run,
                existing_reference=existing_reference,
                attach_to_existing=True,
            )
            for record in records:
                record_processed(record)

        return summary
