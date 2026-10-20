"""Client for the Pour la Science archives website."""

from .client import AuthenticationError, PourLaScienceClient, PourLaScienceError
from .config import PourLaScienceConfig
from .archives import MagazineArticle, MagazineIssue, parse_issue_page
from .zotero_importer import (
    IssueImportSummary,
    PourLaScienceZoteroImporter,
)

__all__ = [
    "AuthenticationError",
    "MagazineArticle",
    "MagazineIssue",
    "IssueImportSummary",
    "PourLaScienceClient",
    "PourLaScienceConfig",
    "PourLaScienceError",
    "PourLaScienceZoteroImporter",
    "parse_issue_page",
]
