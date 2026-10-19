"""Unit tests for pdf_importer module."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pdf_importer.extractor import (
    ExtractedMetadata,
    clean_doi,
    find_doi_in_text,
    parse_filename_heuristics,
)
from pdf_importer.indexer import ZoteroLibraryIndex, normalize_title
from pdf_importer.importer import PDFImporter


def test_clean_doi():
    assert clean_doi("https://doi.org/10.1289/EHP2427") == "10.1289/EHP2427"
    assert clean_doi("http://doi.org/10.1000/182.") == "10.1000/182"
    assert clean_doi("doi:10.1038/nature12345,") == "10.1038/nature12345"
    assert clean_doi("10.1016/j.cell.2020.01.001)") == "10.1016/j.cell.2020.01.001"


def test_find_doi_in_text():
    text = "Published in Nature. Full text available at https://doi.org/10.1038/s41586-020-2003-7 for readers."
    assert find_doi_in_text(text) == "10.1038/s41586-020-2003-7"

    no_doi = "Some article with no identifier."
    assert find_doi_in_text(no_doi) is None


def test_parse_filename_heuristics():
    path1 = Path("/tmp/Articles/Science/2021/Smith, John - Quantum Supremacy.pdf")
    meta1 = parse_filename_heuristics(path1)
    assert meta1.title == "Quantum Supremacy"
    assert meta1.date == "2021"
    assert meta1.publication_title == "Science"
    assert len(meta1.creators) == 1
    assert meta1.creators[0]["lastName"] == "Smith"
    assert meta1.creators[0]["firstName"] == "John"

    path2 = Path("/tmp/Articles/1999/Turing - Computing Machinery.pdf")
    meta2 = parse_filename_heuristics(path2)
    assert meta2.title == "Computing Machinery"
    assert meta2.date == "1999"
    assert meta2.creators[0]["lastName"] == "Turing"

    path3 = Path("/tmp/simple_article.pdf")
    meta3 = parse_filename_heuristics(path3)
    assert meta3.title == "simple_article"
    assert meta3.creators == []


def test_extracted_metadata_to_payload():
    meta = ExtractedMetadata(
        title="Sample Paper",
        creators=[{"creatorType": "author", "firstName": "Alice", "lastName": "Wonderland"}],
        date="2020",
        doi="10.1234/sample",
        publication_title="Test Journal",
    )
    payload = meta.to_zotero_item_payload()
    assert payload["title"] == "Sample Paper"
    assert payload["itemType"] == "journalArticle"
    assert payload["date"] == "2020"
    assert payload["DOI"] == "10.1234/sample"
    assert payload["publicationTitle"] == "Test Journal"
    assert len(payload["creators"]) == 1


def test_zotero_library_index_duplicates(tmp_path):
    index = ZoteroLibraryIndex()
    index.filenames.add("existing_paper.pdf")
    index.dois.add("10.1000/182")
    index.md5_hashes.add("d41d8cd98f00b204e9800998ecf8427e")
    index.title_author_pairs.add((normalize_title("Deep Learning"), "lecun"))

    # Test filename duplicate
    dup, reason = index.check_duplicate(Path("/path/to/existing_paper.pdf"))
    assert dup is True
    assert "Filename already exists" in reason

    # Test clean new file
    dup, reason = index.check_duplicate(Path("/path/to/new_paper.pdf"))
    assert dup is False

    # Test MD5 duplicate
    dup, reason = index.check_duplicate(
        Path("/path/to/new_name.pdf"),
        md5_hash="d41d8cd98f00b204e9800998ecf8427e",
    )
    assert dup is True
    assert "MD5 checksum" in reason

    # Test DOI duplicate
    meta_doi = ExtractedMetadata(title="Unrelated Title", doi="10.1000/182")
    dup, reason = index.check_duplicate(Path("/path/to/other.pdf"), metadata=meta_doi)
    assert dup is True
    assert "DOI already in library" in reason

    # Test Title + Author duplicate
    meta_ta = ExtractedMetadata(
        title="Deep Learning!",
        creators=[{"creatorType": "author", "lastName": "LeCun"}],
    )
    dup, reason = index.check_duplicate(Path("/path/to/another.pdf"), metadata=meta_ta)
    assert dup is True
    assert "Title & author" in reason


def test_importer_dry_run_duplicate_and_new(tmp_path):
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content")

    client_mock = MagicMock()
    index = ZoteroLibraryIndex()
    importer = PDFImporter(client=client_mock, index=index)
    importer.index_loaded = True

    # 1. First run: should be new (dry-run)
    record = importer.import_single_pdf(pdf_file, dry_run=True, use_crossref=False)
    assert record.status == "dry_run_import"

    # 2. Simulate it being registered
    index.filenames.add("test.pdf")

    # 3. Second run: should be skipped as duplicate
    record2 = importer.import_single_pdf(pdf_file, dry_run=True, use_crossref=False)
    assert record2.status == "skipped_duplicate"
