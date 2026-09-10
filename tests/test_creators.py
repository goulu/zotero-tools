"""Unit tests for CreatorCleaner."""

from zotero_cleaner.cleaners.creators import CreatorCleaner
from zotero_cleaner.models import ZoteroItem, ItemData, Creator


def test_split_single_field_name():
    cleaner = CreatorCleaner()
    item = ZoteroItem(
        key="K1",
        version=1,
        data=ItemData(
            key="K1",
            version=1,
            itemType="book",
            title="Book Title",
            creators=[Creator(name="Curie, Marie")],
        ),
    )
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value[0]["firstName"] == "Marie"
    assert issues[0].proposed_value[0]["lastName"] == "Curie"


def test_remove_et_al():
    cleaner = CreatorCleaner()
    item = ZoteroItem(
        key="K2",
        version=1,
        data=ItemData(
            key="K2",
            version=1,
            itemType="journalArticle",
            title="Article Title",
            creators=[
                Creator(lastName="Einstein", firstName="Albert"),
                Creator(lastName="et al."),
            ],
        ),
    )
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert len(issues[0].proposed_value) == 1
    assert issues[0].proposed_value[0]["lastName"] == "Einstein"


def test_clean_all_caps_author():
    cleaner = CreatorCleaner()
    item = ZoteroItem(
        key="K3",
        version=1,
        data=ItemData(
            key="K3",
            version=1,
            itemType="book",
            title="Title",
            creators=[Creator(lastName="POINCARE", firstName="HENRI")],
        ),
    )
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value[0]["lastName"] == "Poincare"
    assert issues[0].proposed_value[0]["firstName"] == "Henri"
