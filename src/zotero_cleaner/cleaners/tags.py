"""Cleaner for Zotero tags."""

from zotero_cleaner.cleaners.base import BaseCleaner, CleanIssue
from zotero_cleaner.models import ZoteroItem


class TagCleaner(BaseCleaner):
    def __init__(self, remove_automatic: bool = True, strip_tags: bool = True):
        self.remove_automatic = remove_automatic
        self.strip_tags = strip_tags

    def analyze(self, item: ZoteroItem) -> list[CleanIssue]:
        tags = item.data.tags
        if not tags:
            return []

        modified = False
        new_tags: list[dict] = []
        removed_tags: list[str] = []

        for t in tags:
            tag_name = t.tag.strip() if self.strip_tags else t.tag
            tag_type = t.type or 0

            # Strip empty tags
            if not tag_name:
                modified = True
                continue

            # Remove automatically imported tags
            if self.remove_automatic and tag_type == 1:
                modified = True
                removed_tags.append(tag_name)
                continue

            new_tags.append({"tag": tag_name, "type": tag_type})

        if modified:
            desc = f"{len(removed_tags)} automatic tag(s) removed" if removed_tags else "Tags cleaned"
            return [
                CleanIssue(
                    item_key=item.key,
                    item_version=item.version,
                    item_title=item.title,
                    issue_type="tag_cleaning",
                    description=desc,
                    original_value=[t.model_dump() for t in tags],
                    proposed_value=new_tags,
                    patch_data={"tags": new_tags},
                    can_auto_fix=True,
                )
            ]
        return []
