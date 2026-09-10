"""Unit tests for DuplicateDetector."""

from zotero_cleaner.cleaners.duplicates import DuplicateDetector, normalize_string
from zotero_cleaner.models import ZoteroItem, ItemData, Creator


def test_normalize_string():
    assert normalize_string("Café & Thé: L'Avenir!") == "cafethelavenir"


def test_doi_duplicates():
    item1 = ZoteroItem(
        key="KEY1",
        version=1,
        data=ItemData(
            key="KEY1",
            version=1,
            itemType="journalArticle",
            title="First Version",
            DOI="https://doi.org/10.1000/182",
        ),
    )
    item2 = ZoteroItem(
        key="KEY2",
        version=2,
        data=ItemData(
            key="KEY2",
            version=2,
            itemType="journalArticle",
            title="Second Version (duplicate)",
            DOI="10.1000/182",
            abstractNote="Longer abstract with more details",
        ),
    )
    detector = DuplicateDetector()
    groups = detector.find_duplicates([item1, item2])
    assert len(groups) == 1
    assert groups[0].criterion == "DOI"
    assert groups[0].master_item.key == "KEY2"


def test_title_year_author_duplicates():
    item1 = ZoteroItem(
        key="K1",
        version=1,
        data=ItemData(
            key="K1",
            version=1,
            itemType="book",
            title="The Feynman Lectures on Physics",
            date="1964",
            creators=[Creator(lastName="Feynman", firstName="Richard")],
        ),
    )
    item2 = ZoteroItem(
        key="K2",
        version=1,
        data=ItemData(
            key="K2",
            version=1,
            itemType="book",
            title="The Feynman Lectures on Physics!",
            date="1964-05",
            creators=[Creator(lastName="Feynman", firstName="R. P.")],
        ),
    )
    detector = DuplicateDetector()
    groups = detector.find_duplicates([item1, item2])
    assert len(groups) == 1
    assert groups[0].criterion == "Title + Year + Author"
