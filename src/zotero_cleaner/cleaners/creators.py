"""Cleaner for Zotero creators and authors."""

from zotero_cleaner.cleaners.base import BaseCleaner, CleanIssue
from zotero_cleaner.models import ZoteroItem


def clean_name_casing(name: str) -> str:
    """Capitalize words in ALL-CAPS names (e.g. DUPONT -> Dupont)."""
    words = name.split()
    cleaned_words = []
    for w in words:
        if len(w) > 1 and w.isupper():
            cleaned_words.append(w.capitalize())
        else:
            cleaned_words.append(w)
    return " ".join(cleaned_words)


class CreatorCleaner(BaseCleaner):
    def analyze(self, item: ZoteroItem) -> list[CleanIssue]:
        creators = item.data.creators
        if not creators:
            return []

        modified = False
        new_creators: list[dict] = []
        descriptions: list[str] = []

        for c in creators:
            c_type = c.creatorType
            first = (c.firstName or "").strip()
            last = (c.lastName or "").strip()
            name = (c.name or "").strip()

            # 1. Single-field "Lastname, Firstname"
            if name and not first and not last and "," in name:
                parts = [p.strip() for p in name.split(",", 1)]
                last = parts[0]
                first = parts[1]
                name = None
                modified = True
                descriptions.append(f"Split Last/First name for '{last}, {first}'")

            # 2. Lingering "et al." or "others" as author
            if (last and last.lower() in {"et al.", "et al", "others"}) or (name and name.lower() in {"et al.", "et al", "others"}):
                modified = True
                descriptions.append("Removed lingering 'et al.' author entry")
                continue

            # 3. Fix ALL-CAPS names
            if last and last.isupper() and len(last) > 1:
                new_last = clean_name_casing(last)
                if new_last != last:
                    last = new_last
                    modified = True
                    descriptions.append(f"Fixed casing for lastname '{last}'")

            if first and first.isupper() and len(first) > 1:
                new_first = clean_name_casing(first)
                if new_first != first:
                    first = new_first
                    modified = True
                    descriptions.append(f"Fixed casing for firstname '{first}'")

            entry = {"creatorType": c_type}
            if name:
                entry["name"] = name
            else:
                entry["firstName"] = first
                entry["lastName"] = last
            new_creators.append(entry)

        if modified:
            return [
                CleanIssue(
                    item_key=item.key,
                    item_version=item.version,
                    item_title=item.title,
                    issue_type="creator_formatting",
                    description="; ".join(descriptions),
                    original_value=[c.model_dump() for c in creators],
                    proposed_value=new_creators,
                    patch_data={"creators": new_creators},
                    can_auto_fix=True,
                )
            ]
        return []
