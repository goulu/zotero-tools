"""Unit tests for AttachmentCleaner."""

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
