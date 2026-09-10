# Zotero Cleaner 🧹

A Python tool to audit and clean your Zotero bibliography by communicating directly with the local Zotero HTTP API (`http://localhost:23119/api`).

Designed for **maximum safety** with a **Dry-Run (simulation) mode by default**.

---

## Features

- **Direct local connection**: Interacts with your running Zotero instance without needing cloud sync or external API keys.
- **Local write authorization (`auth`)**: Automatically requests and persists a local API key for safe write operations in Zotero 10+.
- **General health audit (`audit`)**: High-level overview of metadata quality (titles, authors, tags, duplicate references and attachments).
- **Duplicate reference detection (`duplicates`)**:
  - Exact DOI matching (normalized)
  - Exact ISBN matching
  - Fuzzy matching (Normalized Title + Publication Year + First Author)
  - Automatic detection of the most complete "master" item (metadata score, child attachments)
  - Optional tagging with `zotero-cleaner:duplicate`
- **Duplicate attachment removal (`clean-attachments`)**:
  - Detects duplicate PDF files, snapshots, or web links attached to the same parent reference
  - Detection via MD5 file checksums, filenames, or snapshot URLs
  - Automatically preserves the original/oldest attachment and marks redundant copies
  - Safely moves duplicates to Zotero's **Trash** with user confirmation
  - Optional alternative: tag duplicates with `zotero-cleaner:duplicate-attachment`
- **Title cleanup (`clean-titles`)**:
  - Automatically extracts embedded bibliographic citations in titles (e.g., `"Author (Year) : Title Publisher, ISBN..."`):
    - Cleans the title to keep only the pure document title
    - Populates the reference's `creators` (authors) if empty
    - Populates `date` (publication year) if empty
    - Extracts and populates `ISBN` if empty
  - Converts ALL-CAPS titles to Sentence case
  - Strips HTML tags (`<i>`, `<b>`, `<jats:...>`) and unescapes entities (`&amp;`)
  - Normalizes multiple spaces, tabs, and newline characters
  - Removes unnecessary trailing periods (while preserving initials like "A.")
- **Creator / author standardization**:
  - Splits single-field "Lastname, Firstname" into proper first and last name fields
  - Corrects ALL-CAPS names
  - Removes trailing "et al." residues imported into author lists
- **Tag cleanup (`clean-tags`)**:
  - Removes automatically imported tags from connectors without touching your manual tags
- **Statistics (`stats`) & Connectivity check (`ping`)**:
  - Displays total item count, top-level references, attachments, and notes.

---

## Installation & Setup

The project uses a Python virtual environment in `.venv`.

To activate it in your terminal:

```bash
source .venv/bin/activate
```

If you need to install or update dependencies:
```bash
pip install -r requirements.txt
pip install -e .
```

---

## Configuration

A `.env` file is initialized at the root of the project:

```dotenv
ZOTERO_API_URL=http://localhost:23119/api
ZOTERO_USER_ID=0
ZOTERO_API_KEY=
ZOTERO_TIMEOUT=30
```

---

## CLI Commands

Once `.venv` is activated (or via `./.venv/bin/zotero-cleaner`):

### 1. Test connection to Zotero
```bash
zotero-cleaner ping
```

### 2. Authorize local write access (Required before first modification/deletion)
```bash
zotero-cleaner auth
```
> A dialog prompt will pop up in your Zotero application: click **"Always Allow"** (or "Allow"). The local key will automatically be saved into your `.env` file.

### 3. Display library statistics
```bash
zotero-cleaner stats
```

### 4. Run a general health audit (100 references sample by default)
```bash
zotero-cleaner audit --limit 100

# Or audit the entire library:
zotero-cleaner audit --limit 0
```

### 5. Find duplicate references
```bash
# Dry-run: inspect potential duplicates
zotero-cleaner duplicates --limit 1000

# Tag duplicate references in Zotero (with confirmation prompt):
zotero-cleaner duplicates --limit 1000 --tag --apply
```

### 6. Remove duplicate attachments (PDFs, snapshots)
```bash
# Dry-run: inspect duplicate attachments under the same parent item
zotero-cleaner clean-attachments --limit 500

# Analyze all attachments in your library:
zotero-cleaner clean-attachments --limit 0

# Move duplicates to Zotero Trash (with interactive confirmation):
zotero-cleaner clean-attachments --limit 0 --apply

# Alternative: tag duplicates instead of moving them to trash
zotero-cleaner clean-attachments --limit 0 --tag --apply
```

### 7. Clean titles
```bash
# Dry-run: show proposed title changes before/after
zotero-cleaner clean-titles --limit 200

# Apply corrections in Zotero:
zotero-cleaner clean-titles --limit 200 --apply
```

### 8. Clean automatic tags
```bash
# Dry-run
zotero-cleaner clean-tags --limit 200

# Apply tag cleanup
zotero-cleaner clean-tags --limit 200 --apply
```

---

## Running Tests

To run the automated test suite:

```bash
pytest -v
```
