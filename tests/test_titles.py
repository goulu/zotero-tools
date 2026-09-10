"""Unit tests for TitleCleaner."""

from zotero_cleaner.cleaners.titles import TitleCleaner
from zotero_cleaner.models import ZoteroItem, ItemData


def make_test_item(title: str, key: str = "ITEM01") -> ZoteroItem:
    return ZoteroItem(
        key=key,
        version=1,
        data=ItemData(
            key=key,
            version=1,
            itemType="journalArticle",
            title=title,
        ),
    )


def test_html_and_space_stripping():
    cleaner = TitleCleaner()
    item = make_test_item("<i>Quantum</i>   Gravity   &amp; Spacetime.\n")
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value == "Quantum Gravity & Spacetime"


def test_all_caps_title():
    cleaner = TitleCleaner(fix_all_caps=True)
    item = make_test_item("A COMPREHENSIVE STUDY OF BLACK HOLES")
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value == "A comprehensive study of black holes"


def test_bibtex_brackets():
    cleaner = TitleCleaner()
    item = make_test_item("{On the Electrodynamics of Moving Bodies}")
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value == "On the Electrodynamics of Moving Bodies"


def test_trailing_dot():
    cleaner = TitleCleaner(strip_trailing_dot=True)
    item = make_test_item("Relativity: The Special and General Theory.")
    issues = cleaner.analyze(item)
    assert len(issues) == 1
    assert issues[0].proposed_value == "Relativity: The Special and General Theory"

    # Should not strip dot after single-letter initial
    item_initial = make_test_item("Notes on Physics by Feynman, R. P.")
    issues_initial = cleaner.analyze(item_initial)
    assert len(issues_initial) == 0


def test_extract_embedded_citation():
    cleaner = TitleCleaner(extract_citations=True)
    raw_title = "Frédéric Leclerc (2018) : Un temps pour l’éternité Éd livre, 2018, ISBN: 978-2414258000."
    item = make_test_item(raw_title)

    issues = cleaner.analyze(item)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.proposed_value == "Un temps pour l’éternité"
    assert issue.patch_data["title"] == "Un temps pour l’éternité"
    assert issue.patch_data["date"] == "2018"
    assert issue.patch_data["ISBN"] == "9782414258000"
    assert len(issue.patch_data["creators"]) == 1
    assert issue.patch_data["creators"][0]["firstName"] == "Frédéric"
    assert issue.patch_data["creators"][0]["lastName"] == "Leclerc"

