"""Unit tests for pdf_importer module."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pdf_importer.extractor import (
    ExtractedMetadata,
    clean_doi,
    fetch_crossref_metadata_by_title,
    find_doi_in_text,
    parse_filename_heuristics,
)
from pdf_importer.indexer import ZoteroLibraryIndex, normalize_title
from pdf_importer.importer import PDFImporter
from zotero_cleaner.models import ZoteroItem


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


def test_crossref_title_lookup_returns_matching_metadata(monkeypatch):
    import pdf_importer.extractor as extractor

    response_data = {
        "message": {
            "items": [{
                "title": ["A Study of Sample Systems"],
                "author": [{"given": "Alex", "family": "Example"}],
                "DOI": "10.1234/sample",
                "ISBN": ["978-1-23456-789-0"],
                "type": "book",
            }]
        }
    }

    class Response:
        status_code = 200

        def json(self):
            return response_data

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params, headers):
            assert params == {
                "query.title": "A Study of Sample Systems",
                "rows": 5,
            }
            return Response()

    monkeypatch.setattr(extractor.httpx, "Client", Client)
    metadata = fetch_crossref_metadata_by_title("A Study of Sample Systems")

    assert metadata is not None
    assert metadata.creators[0]["lastName"] == "Example"
    assert metadata.doi == "10.1234/sample"
    assert metadata.isbn == "978-1-23456-789-0"


def test_crossref_title_lookup_rejects_equivalent_matches(monkeypatch):
    import pdf_importer.extractor as extractor

    class Response:
        status_code = 200

        def json(self):
            return {
                "message": {
                    "items": [
                        {"title": ["The Same Book Title"], "DOI": "10.1/a"},
                        {"title": ["The Same Book Title"], "DOI": "10.1/b"},
                    ]
                }
            }

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(extractor.httpx, "Client", Client)

    assert fetch_crossref_metadata_by_title("The Same Book Title") is None


def test_extract_pdf_metadata_looks_up_title_when_author_missing(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace
    import pdf_importer.extractor as extractor

    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content")
    reader = SimpleNamespace(
        metadata=SimpleNamespace(
            subject=None,
            title="A Proper Title",
            keywords=None,
            author=None,
            creation_date=None,
        ),
        pages=[],
    )
    looked_up = ExtractedMetadata(
        title="A Proper Title",
        creators=[{"creatorType": "author", "lastName": "Web Author"}],
        doi="10.1234/sample",
        isbn="978-1-23456-789-0",
        source="crossref",
    )
    monkeypatch.setattr(extractor, "PdfReader", lambda path: reader)
    monkeypatch.setattr(
        extractor,
        "fetch_crossref_metadata_by_title",
        lambda title, timeout: (
            looked_up if title == "A Proper Title" else None
        ),
    )

    metadata = extractor.extract_pdf_metadata(pdf_file)

    assert metadata.creators[0]["lastName"] == "Web Author"
    assert metadata.doi == "10.1234/sample"
    assert metadata.isbn == "978-1-23456-789-0"


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


@pytest.mark.parametrize("dry_run", [True, False])
def test_importer_enriches_zotero_reference_missing_author(tmp_path, monkeypatch, dry_run):
    import pdf_importer.importer as importer_module

    pdf_file = tmp_path / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content")
    reference = ZoteroItem.model_validate({
        "key": "ABC123",
        "version": 1,
        "data": {
            "key": "ABC123",
            "version": 1,
            "itemType": "journalArticle",
            "title": "Sample Paper",
            "creators": [],
        },
    })
    index = ZoteroLibraryIndex()
    index._index_reference(reference)
    client = MagicMock()
    importer = PDFImporter(client=client, index=index)
    importer.index_loaded = True
    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        lambda *args, **kwargs: ExtractedMetadata(
            title="Sample Paper",
            creators=[{"creatorType": "author", "lastName": "PDF Author"}],
        ),
    )
    monkeypatch.setattr(
        importer_module,
        "fetch_crossref_metadata_by_title",
        lambda *args, **kwargs: ExtractedMetadata(
            title="Sample Paper",
            creators=[{"creatorType": "author", "lastName": "Web Author"}],
            doi="10.1234/sample",
            abstract_note="Sample abstract",
            volume="3",
            pages="5-10",
        ),
    )

    record = importer.import_single_pdf(pdf_file, dry_run=dry_run, compute_md5=False)

    assert record.status == "skipped_duplicate"
    if dry_run:
        assert "would be enriched" in record.reason
        client.update_item_data.assert_not_called()
    else:
        client.update_item_data.assert_called_once_with(
            "ABC123",
            1,
            {
                "creators": [{"creatorType": "author", "lastName": "Web Author"}],
                "DOI": "10.1234/sample",
                "abstractNote": "Sample abstract",
                "volume": "3",
                "pages": "5-10",
            },
        )


@pytest.mark.parametrize("use_crossref", [True, False])
def test_find_book_groups_uses_folder_and_filename_author(
    tmp_path,
    monkeypatch,
    use_crossref,
):
    import pdf_importer.importer as importer_module

    book_dir = tmp_path / "The C Programming Language" / "1997"
    chapter_paths = [
        book_dir / "Stroustrup - 10 Classes.pdf",
        book_dir / "Stroustrup, Library, Stroustrup - 11 Templates.pdf",
    ]
    importer = PDFImporter(client=MagicMock(), index=ZoteroLibraryIndex())
    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        lambda path, **kwargs: ExtractedMetadata(
            title=path.stem.split(" - ", 1)[1],
            creators=[{"creatorType": "author", "lastName": "Stroustrup"}],
            source="filename_heuristic",
        ),
    )
    monkeypatch.setattr(
        importer_module,
        "fetch_crossref_metadata_by_title",
        lambda *args, **kwargs: None,
    )

    groups, _ = importer._find_book_groups(
        chapter_paths,
        use_crossref=use_crossref,
    )

    assert len(groups) == 1
    grouped_paths, metadata = groups[0]
    assert grouped_paths == chapter_paths
    assert metadata.title == "The C Programming Language"
    assert metadata.date == "1997"
    assert metadata.item_type == "book"
    assert metadata.creators[0]["lastName"] == "Stroustrup"


def test_import_directory_dry_run_groups_book_chapters(tmp_path, monkeypatch):
    import pdf_importer.importer as importer_module

    book_dir = tmp_path / "The C Programming Language" / "1997"
    book_dir.mkdir(parents=True)
    chapter_paths = [
        book_dir / "Stroustrup - Chapter 1.pdf",
        book_dir / "Stroustrup - Chapter 2.pdf",
    ]
    for path in chapter_paths:
        path.write_bytes(b"%PDF-1.4 chapter")

    client = MagicMock()
    importer = PDFImporter(client=client, index=ZoteroLibraryIndex())
    importer.index_loaded = True
    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        lambda path, **kwargs: ExtractedMetadata(
            title=path.stem.split(" - ", 1)[1],
            creators=[{"creatorType": "author", "lastName": "Stroustrup"}],
            source="filename_heuristic",
        ),
    )
    monkeypatch.setattr(
        importer_module,
        "fetch_crossref_metadata_by_title",
        lambda *args, **kwargs: None,
    )

    summary = importer.import_directory(
        book_dir,
        recursive=False,
        dry_run=True,
        max_files=2,
    )

    assert summary.total_scanned == 2
    assert summary.imported == 2
    assert all(record.status == "dry_run_import" for record in summary.records)
    assert all(record.metadata.item_type == "book" for record in summary.records)
    assert all(record.metadata.title == "The C Programming Language" for record in summary.records)
    assert all("Would create book" in record.reason for record in summary.records)
    client.create_items.assert_not_called()


def test_create_zotero_book_entry_attaches_all_pdfs(tmp_path):
    from pdf_importer.config import PDFImporterConfig

    file_paths = [tmp_path / "chapter-1.pdf", tmp_path / "chapter-2.pdf"]
    for file_path in file_paths:
        file_path.write_bytes(b"pdf")
    client = MagicMock()
    client.create_items.side_effect = [
        {"successful": {"0": {"key": "BOOK1", "version": 3}}},
        {
            "successful": {
                "0": {"key": "ATT1"},
                "1": {"key": "ATT2"},
            }
        },
    ]
    config = PDFImporterConfig(storage_dir=tmp_path / "storage")
    importer = PDFImporter(
        client=client,
        config=config,
        index=ZoteroLibraryIndex(),
    )
    metadata = ExtractedMetadata(
        title="The C Programming Language",
        creators=[{"creatorType": "author", "lastName": "Stroustrup"}],
        item_type="book",
    )

    parent_key, attached, errors = importer._create_zotero_book_entry(
        file_paths,
        metadata,
        {},
    )

    assert parent_key == "BOOK1"
    assert attached == {file_paths[0]: "ATT1", file_paths[1]: "ATT2"}
    assert errors == {}
    assert len(client.create_items.call_args_list[1].args[0]) == 2
    assert (tmp_path / "storage" / "ATT1" / file_paths[0].name).exists()
    assert (tmp_path / "storage" / "ATT2" / file_paths[1].name).exists()
