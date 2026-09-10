"""Cleaning rules for document titles."""

import html
import re
from zotero_cleaner.cleaners.base import BaseCleaner, CleanIssue
from zotero_cleaner.models import ZoteroItem


class TitleCleaner(BaseCleaner):
    def __init__(self, fix_all_caps: bool = True, strip_trailing_dot: bool = True):
        self.fix_all_caps = fix_all_caps
        self.strip_trailing_dot = strip_trailing_dot

    def clean_string(self, title: str) -> tuple[str, list[str]]:
        """Clean title string and return transformed title along with reasons."""
        reasons = []
        new_title = title

        # 1. Decode HTML entities (e.g. &amp; -> &)
        decoded = html.unescape(new_title)
        if decoded != new_title:
            reasons.append("Decoded HTML entities")
            new_title = decoded

        # 2. Strip HTML tags (e.g. <i>, </i>, <b>, <jats:...>)
        without_html = re.sub(r"<[^>]+>", "", new_title)
        if without_html != new_title:
            reasons.append("Removed HTML tags")
            new_title = without_html

        # 3. Strip enclosing BibTeX brackets ({My Title} -> My Title)
        if new_title.startswith("{") and new_title.endswith("}"):
            new_title = new_title[1:-1]
            reasons.append("Removed enclosing BibTeX braces")

        # 4. Normalize whitespace (newlines, tabs, multiple consecutive spaces)
        normalized_spaces = " ".join(new_title.split())
        if normalized_spaces != new_title:
            reasons.append("Normalized whitespace & line breaks")
            new_title = normalized_spaces

        # 5. ALL-CAPS detection
        letters = [c for c in new_title if c.isalpha()]
        words = new_title.split()
        if self.fix_all_caps and len(words) >= 2 and len(letters) >= 8:
            if all(c.isupper() for c in letters):
                reasons.append("Converted ALL-CAPS to Sentence case")
                new_title = new_title[0].upper() + new_title[1:].lower()

        # 6. Unnecessary trailing dot removal
        if self.strip_trailing_dot and new_title.endswith(".") and not new_title.endswith(".."):
            # Do not strip if preceded by a single initial or recognized abbreviation
            is_initial = bool(re.search(r"(?:^|[\s,;:(])([A-Za-z])\.$", new_title))
            is_abbr = any(new_title.lower().endswith(abbr) for abbr in ["etc.", "al.", "inc.", "co.", "jr.", "sr.", "ed.", "vol."])
            if not is_initial and not is_abbr:
                new_title = new_title[:-1].rstrip()
                reasons.append("Removed trailing period")

        return new_title, reasons

    def analyze(self, item: ZoteroItem) -> list[CleanIssue]:
        title = item.title
        if not title:
            return []

        new_title, reasons = self.clean_string(title)
        if new_title != title:
            return [
                CleanIssue(
                    item_key=item.key,
                    item_version=item.version,
                    item_title=item.title,
                    issue_type="title_formatting",
                    description="; ".join(reasons),
                    original_value=title,
                    proposed_value=new_title,
                    patch_data={"title": new_title},
                    can_auto_fix=True,
                )
            ]
        return []
