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


def test_crossref_title_lookup_includes_author(monkeypatch):
    import pdf_importer.extractor as extractor

    class Response:
        status_code = 200

        def json(self):
            return {
                "message": {
                    "items": [{
                        "title": ["A Historic Cosmology Article"],
                        "author": [{"given": "Georges", "family": "Lemaitre"}],
                        "DOI": "10.1234/lemaitre",
                    }]
                }
            }

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, params, headers):
            assert params["query.title"] == "A Historic Cosmology Article"
            assert params["query.author"] == "Georges Lemaître"
            return Response()

    monkeypatch.setattr(extractor.httpx, "Client", Client)
    metadata = fetch_crossref_metadata_by_title(
        "A Historic Cosmology Article",
        author="Georges Lemaître",
    )

    assert metadata is not None
    assert metadata.doi == "10.1234/lemaitre"


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


@pytest.mark.parametrize(
    ("folder_name", "expected_item_type"),
    [("_Book", "book"), ("_Patents", "patent")],
)
def test_extract_pdf_metadata_uses_special_folder_item_type(
    tmp_path,
    monkeypatch,
    folder_name,
    expected_item_type,
):
    from types import SimpleNamespace
    import pdf_importer.extractor as extractor

    pdf_file = tmp_path / folder_name / "Example.pdf"
    pdf_file.parent.mkdir()
    pdf_file.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        extractor,
        "PdfReader",
        lambda path: SimpleNamespace(metadata=None, pages=[]),
    )

    metadata = extractor.extract_pdf_metadata(pdf_file, use_crossref=False)

    assert metadata.item_type == expected_item_type


@pytest.mark.parametrize(
    ("relative_path", "expected_type", "expected_title", "expected_author"),
    [
        (
            "Nature/2020/Smith-Journal article.pdf",
            "journalArticle",
            "Journal article",
            "Smith",
        ),
        (
            "Book_/1998/Lemaitre-A book title.pdf",
            "book",
            "A book title",
            "Lemaitre",
        ),
        (
            "_Patents/US1234567.pdf",
            "patent",
            "US1234567",
            None,
        ),
    ],
)
def test_path_rules_extract_default_metadata(
    tmp_path,
    monkeypatch,
    relative_path,
    expected_type,
    expected_title,
    expected_author,
):
    from types import SimpleNamespace
    import pdf_importer.extractor as extractor

    pdf_file = tmp_path / relative_path
    pdf_file.parent.mkdir(parents=True)
    pdf_file.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(
        extractor,
        "PdfReader",
        lambda path: SimpleNamespace(metadata=None, pages=[]),
    )

    metadata = extractor.extract_pdf_metadata(pdf_file, use_crossref=False)
    payload = metadata.to_zotero_item_payload()

    assert metadata.item_type == expected_type
    assert metadata.title == expected_title
    if expected_author:
        assert metadata.creators[0]["lastName"] == expected_author
    if expected_type == "journalArticle":
        assert metadata.date == "2020"
        assert metadata.publication_title == "Nature"
    if expected_type == "patent":
        assert payload["patentNumber"] == "US1234567"


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
def test_extract_metadata_builds_book_fallback_from_folder_and_author(
    tmp_path,
    monkeypatch,
    use_crossref,
):
    import pdf_importer.importer as importer_module

    book_dir = tmp_path / "The C Programming Language" / "1997"
    chapter_path = book_dir / "Stroustrup - 10 Classes.pdf"
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

    metadata, book_fallback = importer._extract_metadata_or_book_fallback(
        chapter_path,
        use_crossref=use_crossref,
        allow_book_fallback=True,
    )

    assert metadata.title == "10 Classes"
    assert book_fallback is not None
    group_key, book_metadata = book_fallback
    assert group_key[0] == book_dir.resolve()
    assert book_metadata.title == "The C Programming Language"
    assert book_metadata.date == "1997"
    assert book_metadata.item_type == "book"
    assert book_metadata.creators[0]["lastName"] == "Stroustrup"


def test_single_lemaitre_pdf_stays_an_article(tmp_path, monkeypatch):
    import pdf_importer.importer as importer_module

    pdf_path = tmp_path / "ASSB" / "1927" / "Lemaître - Un Univers homogène.pdf"
    article_metadata = ExtractedMetadata(
        title="Un Univers homogène",
        creators=[{"creatorType": "author", "lastName": "Lemaître"}],
        date="1927",
        publication_title="ASSB",
        item_type="journalArticle",
        source="filename_heuristic",
    )
    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        lambda *args, **kwargs: article_metadata,
    )
    importer = PDFImporter(client=MagicMock(), index=ZoteroLibraryIndex())

    metadata, book_fallback = importer._extract_metadata_or_book_fallback(
        pdf_path,
        use_crossref=False,
        allow_book_fallback=False,
    )

    assert book_fallback is None
    assert metadata.item_type == "journalArticle"
    assert metadata.creators[0]["lastName"] == "Lemaître"


def test_import_directory_does_not_make_book_for_single_pdf(
    tmp_path,
    monkeypatch,
):
    import pdf_importer.importer as importer_module

    journal_dir = tmp_path / "ASSB" / "1927"
    journal_dir.mkdir(parents=True)
    pdf_path = journal_dir / "Lemaître - Un Univers homogène.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 article")
    article_metadata = ExtractedMetadata(
        title="Un Univers homogène",
        creators=[{"creatorType": "author", "lastName": "Lemaître"}],
        date="1927",
        publication_title="ASSB",
        item_type="journalArticle",
        source="filename_heuristic",
    )
    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        lambda *args, **kwargs: article_metadata,
    )
    client = MagicMock()
    importer = PDFImporter(client=client, index=ZoteroLibraryIndex())
    importer.index_loaded = True

    summary = importer.import_directory(
        journal_dir,
        dry_run=True,
        use_crossref=False,
    )

    assert summary.total_scanned == 1
    assert summary.records[0].metadata.item_type == "journalArticle"
    assert summary.records[0].metadata.title == "Un Univers homogène"
    client.create_items.assert_not_called()


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
    extraction_order = []
    processed_counts = []

    def extract_metadata(path, **kwargs):
        assert len(extraction_order) == len(processed_counts)
        extraction_order.append(path)
        return ExtractedMetadata(
            title=path.stem.split(" - ", 1)[1],
            creators=[{"creatorType": "author", "lastName": "Stroustrup"}],
            source="filename_heuristic",
        )

    monkeypatch.setattr(
        importer_module,
        "extract_pdf_metadata",
        extract_metadata,
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
        on_file_processed=lambda record, count: processed_counts.append(count),
    )

    assert summary.total_scanned == 2
    assert extraction_order == chapter_paths
    assert processed_counts == [1, 2]
    assert summary.imported == 2
    assert all(record.status == "dry_run_import" for record in summary.records)
    assert all(record.metadata.item_type == "book" for record in summary.records)
    assert all(record.metadata.title == "The C Programming Language" for record in summary.records)
    assert "Would create book" in summary.records[0].reason
    assert "Would attach to existing book" in summary.records[1].reason
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
