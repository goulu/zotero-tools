"""Unit tests for AttachmentCleaner."""

from pathlib import Path

from zotero_cleaner.cleaners.attachments import AttachmentCleaner
from zotero_cleaner.models import ZoteroItem, ItemData


def make_attachment(
    key: str,
    parent_key: str,
    filename: str = "paper.pdf",
    md5: str = "abc1234567890def",
    content_type: str = "application/pdf",
    date_added: str = "2024-01-01T12:00:00Z",
) -> ZoteroItem:
    return ZoteroItem(
        key=key,
        version=1,
        data=ItemData(
            key=key,
            version=1,
            itemType="attachment",
            title=filename,
            parentItem=parent_key,
            filename=filename,
            contentType=content_type,
            md5=md5,
            dateAdded=date_added,
        ),
    )


def test_duplicate_attachment_same_parent_and_md5():
    att1 = make_attachment("ATT1", "PARENT1", filename="doc.pdf", md5="1111222233334444", date_added="2024-01-01T00:00:00Z")
    att2 = make_attachment("ATT2", "PARENT1", filename="doc.pdf", md5="1111222233334444", date_added="2024-01-02T00:00:00Z")

    cleaner = AttachmentCleaner(scope="parent")
    groups = cleaner.find_duplicates([att1, att2])

    assert len(groups) == 1
    assert groups[0].criterion == "Identical MD5"
    assert groups[0].master_item.key == "ATT1"
    assert len(groups[0].duplicate_items) == 1
    assert groups[0].duplicate_items[0].key == "ATT2"


def test_attachment_different_parents_not_duplicates_in_parent_scope():
    att1 = make_attachment("ATT1", "PARENT1", filename="doc.pdf", md5="1111222233334444")
    att2 = make_attachment("ATT2", "PARENT2", filename="doc.pdf", md5="1111222233334444")

    cleaner = AttachmentCleaner(scope="parent")
    groups = cleaner.find_duplicates([att1, att2])
    assert len(groups) == 0


def test_duplicate_attachment_by_filename_when_no_md5():
    att1 = make_attachment("ATT1", "PARENT1", filename="report.pdf", md5="", date_added="2024-01-01T00:00:00Z")
    att2 = make_attachment("ATT2", "PARENT1", filename="report.pdf", md5="", date_added="2024-01-02T00:00:00Z")

    cleaner = AttachmentCleaner(scope="parent")
    groups = cleaner.find_duplicates([att1, att2])

    assert len(groups) == 1
    assert groups[0].criterion == "Same filename"
    assert groups[0].master_item.key == "ATT1"
    assert groups[0].duplicate_items[0].key == "ATT2"


def test_check_pdf_files_finds_matching_filename_recursively(tmp_path: Path):
    nested_directory = tmp_path / "nested"
    nested_directory.mkdir()
    matching_file = nested_directory / "report.pdf"
    matching_file.touch()
    attachment = make_attachment("ATT1", "PARENT1", filename="report.pdf")
    missing_attachment = make_attachment("ATT2", "PARENT1", filename="missing.pdf")

    checks = AttachmentCleaner().check_pdf_files([attachment, missing_attachment], tmp_path)

    assert [check.found for check in checks] == [True, False]
    assert checks[0].matches == [matching_file]


def test_check_pdf_files_ignores_non_pdf_attachments(tmp_path: Path):
    attachment = make_attachment("ATT1", "PARENT1", filename="snapshot.html", content_type="text/html")

    checks = AttachmentCleaner().check_pdf_files([attachment], tmp_path)

    assert checks == []


def test_find_matching_pdf_requires_the_attachment_md5(tmp_path: Path):
    matching_file = tmp_path / "report.pdf"
    matching_file.write_bytes(b"matching PDF")
    matching_md5 = AttachmentCleaner.file_md5(matching_file)
    attachment = make_attachment("ATT1", "PARENT1", filename="report.pdf", md5=matching_md5)

    check = AttachmentCleaner().check_pdf_files([attachment], tmp_path)[0]

    assert AttachmentCleaner().find_matching_pdf(check) == matching_file


def test_find_matching_pdf_rejects_a_same_named_file_with_different_content(tmp_path: Path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"different PDF")
    attachment = make_attachment("ATT1", "PARENT1", filename="report.pdf", md5="0" * 32)

    check = AttachmentCleaner().check_pdf_files([attachment], tmp_path)

    assert AttachmentCleaner().find_matching_pdf(check[0]) is None
