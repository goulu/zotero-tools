"""Configuration management for PDF Importer."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded
load_dotenv()

DEFAULT_IMPORT_DIR = os.getenv("PDF_IMPORT_DIR", "/home/goulu/moulinette/Documents/Articles")
DEFAULT_LINK_MODE = os.getenv("PDF_IMPORT_MODE", "imported").lower()
DEFAULT_STORAGE_DIR = os.getenv("ZOTERO_STORAGE_DIR", str(Path.home() / "Zotero" / "storage"))


@dataclass
class PDFImporterConfig:
    import_dir: Path = field(default_factory=lambda: Path(DEFAULT_IMPORT_DIR))
    link_mode: str = DEFAULT_LINK_MODE  # 'imported' or 'linked'
    storage_dir: Path = field(default_factory=lambda: Path(DEFAULT_STORAGE_DIR))
    crossref_lookup: bool = True
    crossref_timeout: float = 6.0
    batch_size: int = 50

    def __post_init__(self):
        if isinstance(self.import_dir, str):
            self.import_dir = Path(self.import_dir)
        if isinstance(self.storage_dir, str):
            self.storage_dir = Path(self.storage_dir)
        if self.link_mode not in ("imported", "linked"):
            self.link_mode = "imported"


DEFAULT_IMPORTER_CONFIG = PDFImporterConfig()
