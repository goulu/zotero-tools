"""Base classes for cleaners and issue detectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from zotero_cleaner.models import ZoteroItem


@dataclass
class CleanIssue:
    item_key: str
    item_version: int
    item_title: str
    issue_type: str  # e.g.: "title_formatting", "creator_formatting", etc.
    description: str
    original_value: Any
    proposed_value: Any
    patch_data: dict[str, Any] = field(default_factory=dict)
    can_auto_fix: bool = True


class BaseCleaner(ABC):
    @abstractmethod
    def analyze(self, item: ZoteroItem) -> list[CleanIssue]:
        """Analyze an item and return the list of identified issues."""
        pass
