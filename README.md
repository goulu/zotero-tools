# Zotero Tools 🛠️

A suite of high-performance Python tools to audit, clean, and populate your Zotero bibliography by communicating directly with the local Zotero HTTP API (`http://localhost:23119/api`).

The suite includes two primary tools:
1. **`zotero-cleaner`** 🧹 : Library audit, title/tag cleanup, attachment verification, and duplicate detection/cleanup.
2. **`pdf-importer`** (or `zotero-import-pdf`) 📥 : Fast batch PDF importer with automatic metadata extraction (DOI + CrossRef, PDF Info, directory & filename heuristics) and duplicate prevention.

Designed for **maximum safety** with **dry-run simulation modes**.

---

## Installation & Setup

The project uses a Python virtual environment in `.venv`.

To activate it in your terminal:
```bash
source .venv/bin/activate
```

Install or update the project in editable mode:
```bash
pip install -e ".[dev]"
```

### Configuration (`.env`)

Configure your environment variables in `.env` (or copy from `.env.example`):

```bash
# Zotero local API
ZOTERO_API_URL=http://localhost:23119/api
ZOTERO_USER_ID=0
ZOTERO_API_KEY=your_local_api_key_here
ZOTERO_TIMEOUT=30

# PDF Importer configuration
PDF_IMPORT_DIR=/home/goulu/moulinette/Documents/Articles
PDF_IMPORT_MODE=imported
ZOTERO_STORAGE_DIR=/home/goulu/Zotero/storage
```

---

## 1. PDF Importer (`pdf-importer` / `zotero-import-pdf`)

Scans a directory of PDF files, determines if each file is already present in your Zotero library, extracts bibliographic metadata, and imports new items.

### Features
- **Duplicate Prevention**:
  - Matches existing attachment filenames
  - Matches linked file paths
  - Computes and checks MD5 file checksums
  - Matches DOIs
  - Matches normalized Title + First Author
- **Multi-Level Metadata Extraction**:
  - Detects DOIs in PDF text or metadata, then queries the **CrossRef API** for complete bibliographic details (title, authors, journal, year, volume, issue, pages).
  - If no author is available in the PDF or matching Zotero reference, searches Crossref by title and fills missing author, DOI, ISBN, and bibliographic fields when the title matches closely.
  - Existing Zotero references without authors are enriched in place; `--dry-run` never writes changes.
  - Reads embedded PDF document info (Title, Author, CreationDate).
  - Uses directory and filename heuristics (e.g. `.../<Journal>/<Year>/<Author> - <Title>.pdf`).
- **Flexible Storage Modes**:
  - `imported` (default): creates attachment and stores the file in Zotero's local storage (`~/Zotero/storage/<key>/<file>`).
  - `linked`: creates linked attachments pointing to the existing file path.

### Usage Examples

```bash
# Simulate importing from default directory (/home/goulu/moulinette/Documents/Articles):
pdf-importer import --dry-run

# Test run with maximum 10 PDFs:
pdf-importer import --dry-run --max 10

# Perform the actual import:
pdf-importer import

# Import a specific directory or single file:
pdf-importer import "/path/to/folder" --mode imported

# Check if PDFs in a folder are already in Zotero without importing:
pdf-importer check "/path/to/folder"

# Inspect extracted metadata for a specific PDF:
pdf-importer inspect "/path/to/paper.pdf"
```

---

## 2. Zotero Cleaner (`zotero-cleaner`)

### Features
- **Audit (`audit`)**: General health check of your library metadata.
- **Duplicates (`duplicates`)**: Search for duplicate references by DOI, ISBN, and Title + Author.
- **Attachments (`clean-attachments`)**: Find and clean redundant PDF attachments or snapshots.
- **Titles (`clean-titles`)**: Clean up titles, stripping embedded citations, HTML tags, and ALL CAPS.
- **PDF Verification (`check-pdfs`)**: Verify that attached PDF files exist on disk in Zotero storage and recover missing ones from an external directory if the MD5 matches.
- **Tags (`clean-tags`)**: Remove unwanted automatic tags from connectors.

### Usage Examples

```bash
# Check connection to Zotero
zotero-cleaner ping

# Authorize write access
zotero-cleaner auth

# Display library stats
zotero-cleaner stats

# General library audit
zotero-cleaner audit --limit 100

# Audit duplicate references (dry-run)
zotero-cleaner duplicates --limit 1000

# Tag duplicate references in Zotero
zotero-cleaner duplicates --limit 1000 --tag --apply

# Clean redundant attachments (dry-run)
zotero-cleaner clean-attachments --limit 500

# Move duplicate attachments to Zotero Trash (interactive confirmation)
zotero-cleaner clean-attachments --limit 0 --apply

# Clean title formatting (dry-run)
zotero-cleaner clean-titles --limit 200

# Apply title cleanup
zotero-cleaner clean-titles --limit 200 --apply

# Check local PDF files in Zotero storage
zotero-cleaner check-pdfs

# Restore missing files from local folder when MD5 matches
zotero-cleaner check-pdfs --apply

# Clean automatic tags (dry-run)
zotero-cleaner clean-tags --limit 200

# Apply tag cleanup
zotero-cleaner clean-tags --limit 200 --apply
```

---

## Running Tests

Run the test suite with `pytest`:

```bash
pytest -v
```
