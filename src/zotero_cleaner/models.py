"""Data models for Zotero items and subcomponents."""

from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class Creator(BaseModel):
    creatorType: str = "author"
    firstName: Optional[str] = ""
    lastName: Optional[str] = ""
    name: Optional[str] = None  # Single-field name (e.g. organizations)

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        first = self.firstName or ""
        last = self.lastName or ""
        return f"{last}, {first}".strip(", ")


class Tag(BaseModel):
    tag: str
    type: Optional[int] = 0  # 0 = manual, 1 = automatic


class ItemData(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    version: int
    itemType: str
    title: Optional[str] = ""
    creators: list[Creator] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    date: Optional[str] = ""
    publicationTitle: Optional[str] = ""
    DOI: Optional[str] = None
    ISBN: Optional[str] = None
    ISSN: Optional[str] = None
    url: Optional[str] = None
    abstractNote: Optional[str] = None
    parentItem: Optional[str] = None
    filename: Optional[str] = None
    contentType: Optional[str] = None
    linkMode: Optional[str] = None
    md5: Optional[str] = None
    mtime: Optional[int] = None
    dateAdded: Optional[str] = None
    dateModified: Optional[str] = None


class ZoteroItem(BaseModel):
    key: str
    version: int
    meta: dict[str, Any] = Field(default_factory=dict)
    data: ItemData

    @property
    def is_attachment(self) -> bool:
        return self.data.itemType == "attachment"

    @property
    def parent_key(self) -> Optional[str]:
        return self.data.parentItem

    @property
    def filename(self) -> Optional[str]:
        return self.data.filename

    @property
    def md5(self) -> Optional[str]:
        return self.data.md5

    @property
    def is_attachment_or_note(self) -> bool:
        return self.data.itemType in {"attachment", "note"} or bool(self.data.parentItem)

    @property
    def is_top_level_reference(self) -> bool:
        return not self.is_attachment_or_note

    @property
    def title(self) -> str:
        return self.data.title or ""

    @property
    def doi(self) -> Optional[str]:
        val = self.data.DOI
        if val:
            val = val.strip().lower()
            if val.startswith("https://doi.org/"):
                val = val[len("https://doi.org/"):]
            elif val.startswith("http://doi.org/"):
                val = val[len("http://doi.org/"):]
            elif val.startswith("doi:"):
                val = val[len("doi:"):]
            return val.strip()
        return None

    @property
    def isbn(self) -> Optional[str]:
        if self.data.ISBN:
            return self.data.ISBN.replace("-", "").replace(" ", "").strip()
        return None
