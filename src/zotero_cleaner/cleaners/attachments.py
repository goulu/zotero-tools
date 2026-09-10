"""Detector and cleaner for duplicate Zotero attachments."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from zotero_cleaner.models import ZoteroItem


@dataclass
class DuplicateAttachmentGroup:
    parent_key: Optional[str]
    criterion: str  # "Identical MD5", "Same filename", "Same snapshot URL"
    key_value: str
    items: list[ZoteroItem] = field(default_factory=list)
    master_item: Optional[ZoteroItem] = None

    def determine_master(self):
        """Determine which attachment to keep (master) and which are duplicates."""
        if not self.items:
            return

        def score_item(it: ZoteroItem) -> int:
            score = 0
            if it.data.md5:
                score += 10
            if it.data.filename:
                score += 5
            return score

        # Prioritize higher metadata completeness score, then oldest dateAdded (original attachment)
        sorted_items = sorted(
            self.items,
            key=lambda it: (-score_item(it), it.data.dateAdded or "9999-99-99"),
        )
        self.master_item = sorted_items[0]

    @property
    def duplicate_items(self) -> list[ZoteroItem]:
        if not self.master_item:
            self.determine_master()
        return [it for it in self.items if it.key != (self.master_item.key if self.master_item else "")]


class AttachmentCleaner:
    def __init__(self, scope: str = "parent"):
        """
        :param scope: "parent" (search duplicates attached to the same reference)
                      or "global" (search duplicates across the entire library by MD5)
        """
        self.scope = scope

    def find_duplicates(self, attachments: list[ZoteroItem]) -> list[DuplicateAttachmentGroup]:
        """Analyze a list of attachments and detect duplicate groups."""
        groups: list[DuplicateAttachmentGroup] = []
        already_grouped_keys: set[str] = set()

        if self.scope == "parent":
            by_parent: dict[str, list[ZoteroItem]] = defaultdict(list)
            for att in attachments:
                if not att.is_attachment:
                    continue
                parent = att.parent_key or "NO_PARENT"
                by_parent[parent].append(att)

            for parent_key, parent_atts in by_parent.items():
                if len(parent_atts) < 2:
                    continue

                # 1. Matching non-empty MD5 under this parent
                md5_map: dict[str, list[ZoteroItem]] = defaultdict(list)
                for att in parent_atts:
                    md5 = att.data.md5
                    if md5 and len(md5) >= 16:
                        md5_map[md5].append(att)

                for md5, items in md5_map.items():
                    if len(items) > 1:
                        grp = DuplicateAttachmentGroup(
                            parent_key=parent_key,
                            criterion="Identical MD5",
                            key_value=f"MD5: {md5[:10]}...",
                            items=items,
                        )
                        grp.determine_master()
                        groups.append(grp)
                        for it in items:
                            already_grouped_keys.add(it.key)

                # 2. Matching filename + contentType under this parent
                fn_map: dict[str, list[ZoteroItem]] = defaultdict(list)
                for att in parent_atts:
                    if att.key in already_grouped_keys:
                        continue
                    fn = (att.data.filename or "").strip().lower()
                    ctype = (att.data.contentType or "").strip().lower()
                    if fn and len(fn) > 3:
                        fn_map[f"{fn}::{ctype}"].append(att)

                for fn_key, items in fn_map.items():
                    if len(items) > 1:
                        grp = DuplicateAttachmentGroup(
                            parent_key=parent_key,
                            criterion="Same filename",
                            key_value=fn_key.split("::")[0],
                            items=items,
                        )
                        grp.determine_master()
                        groups.append(grp)
                        for it in items:
                            already_grouped_keys.add(it.key)

                # 3. Matching snapshot URL under this parent
                url_map: dict[str, list[ZoteroItem]] = defaultdict(list)
                for att in parent_atts:
                    if att.key in already_grouped_keys:
                        continue
                    url = (att.data.url or "").strip()
                    if url and len(url) > 10:
                        url_map[url].append(att)

                for url, items in url_map.items():
                    if len(items) > 1:
                        grp = DuplicateAttachmentGroup(
                            parent_key=parent_key,
                            criterion="Same snapshot URL",
                            key_value=url[:40] + ("..." if len(url) > 40 else ""),
                            items=items,
                        )
                        grp.determine_master()
                        groups.append(grp)
                        for it in items:
                            already_grouped_keys.add(it.key)

        else:
            # Global scope: Group by MD5 across all attachments
            global_md5_map: dict[str, list[ZoteroItem]] = defaultdict(list)
            for att in attachments:
                if not att.is_attachment:
                    continue
                md5 = att.data.md5
                if md5 and len(md5) >= 16:
                    global_md5_map[md5].append(att)

            for md5, items in global_md5_map.items():
                if len(items) > 1:
                    grp = DuplicateAttachmentGroup(
                        parent_key=None,
                        criterion="Identical global MD5",
                        key_value=f"MD5: {md5[:10]}...",
                        items=items,
                    )
                    grp.determine_master()
                    groups.append(grp)

        return groups
