"""Command Line Interface for PDF Importer."""

import os
from pathlib import Path
from typing import Optional
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from zotero_cleaner.client import ZoteroClient
from pdf_importer.config import PDFImporterConfig, DEFAULT_IMPORTER_CONFIG
from pdf_importer.extractor import extract_pdf_metadata
from pdf_importer.importer import PDFImporter, discover_pdf_files

app = typer.Typer(
    name="pdf-importer",
    help="High-speed PDF Importer for Zotero with automatic metadata extraction and duplicate detection.",
    add_completion=False,
)
console = Console()


def _get_target_path(path_arg: Optional[str], default_path: Path) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser().resolve()
    else:
        p = default_path
    if not p.exists():
        console.print(f"[bold red]Error:[/] Specified path does not exist: [yellow]{p}[/]")
        raise typer.Exit(code=1)
    return p


@app.command("import")
def import_pdfs(
    path: Optional[str] = typer.Argument(
        None,
        help="Path to directory or single PDF (defaults to PDF_IMPORT_DIR in .env).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate import without modifying the Zotero library.",
    ),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        "-m",
        help="Import mode: 'imported' (copy to Zotero storage) or 'linked' (link file path).",
    ),
    max_files: Optional[int] = typer.Option(
        None,
        "--max",
        help="Maximum number of PDF files to process in this run.",
    ),
    recursive: bool = typer.Option(
        True,
        "--recursive/--no-recursive",
        help="Scan subdirectories recursively.",
    ),
    crossref: bool = typer.Option(
        True,
        "--crossref/--no-crossref",
        help="Query CrossRef API for articles with detected DOI.",
    ),
    quick_index: bool = typer.Option(
        False,
        "--quick-index",
        help="Only index Zotero attachments (skips bibliographic reference scan for speed).",
    ),
):
    """
    Import PDF files into Zotero with bibliographic metadata and duplicate prevention.
    """
    cfg = DEFAULT_IMPORTER_CONFIG
    target_dir = _get_target_path(path, cfg.import_dir)

    if mode:
        cfg.link_mode = mode.lower()

    header_text = (
        f"[bold cyan]Zotero PDF Importer[/]\n"
        f"• Target path: [yellow]{target_dir}[/]\n"
        f"• Mode: [magenta]{cfg.link_mode}[/]\n"
        f"• Dry-run: [{'yellow' if dry_run else 'green'}]{dry_run}[/]\n"
        f"• CrossRef lookup: [blue]{crossref}[/]"
    )
    console.print(Panel(header_text, border_style="cyan"))

    client = ZoteroClient()
    try:
        ping_info = client.ping()
        console.print(f"[green]✓[/] Connected to Zotero (v{ping_info.get('version')})")
    except Exception as e:
        console.print(f"[bold red]Cannot connect to Zotero:[/] {e}")
        raise typer.Exit(code=1)

    importer = PDFImporter(client=client, config=cfg)

    # 1. Build index with progress spinner
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        idx_task = progress.add_task("[cyan]Indexing Zotero library for duplicates...", total=None)

        def on_idx(phase: str, count: int, total: int):
            progress.update(idx_task, description=f"[cyan]Indexing {phase} ({count}/{total})...")

        importer.ensure_index(include_references=not quick_index, on_progress=on_idx)
        progress.update(idx_task, description="[green]Library index loaded successfully!")

    console.print(
        f"[dim]Indexed {len(importer.index.filenames)} attachments, "
        f"{len(importer.index.dois)} DOIs, and {len(importer.index.titles)} reference titles.[/]\n"
    )

    # 2. Process PDFs with interactive progress
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("File", style="cyan", ratio=3)
    table.add_column("Status", ratio=2)
    table.add_column("Title / Reason", ratio=5)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        scan_task = progress.add_task("[yellow]Processing PDFs...", total=max_files)

        def on_file(record, count):
            if record.status in ("imported", "dry_run_import"):
                status_style = "[bold green]IMPORTED[/]" if not dry_run else "[bold yellow]WOULD IMPORT[/]"
                title_disp = record.metadata.title if record.metadata else record.file_path.name
                src_disp = f" [dim]({record.metadata.source})[/]" if record.metadata else ""
                table.add_row(record.file_path.name, status_style, f"{title_disp}{src_disp}")
            elif record.status == "skipped_duplicate":
                table.add_row(record.file_path.name, "[dim yellow]DUPLICATE[/]", f"[dim]{record.reason}[/]")
            elif record.status == "error":
                table.add_row(record.file_path.name, "[bold red]ERROR[/]", f"[red]{record.reason}[/]")

            progress.update(scan_task, advance=1, description=f"[yellow]Processed {count} PDFs...")

        summary = importer.import_directory(
            target_dir,
            recursive=recursive,
            dry_run=dry_run,
            max_files=max_files,
            use_crossref=crossref,
            on_file_processed=on_file,
        )

    # 3. Print Results Table & Summary
    console.print("\n")
    if summary.records:
        console.print(table)

    summary_panel = (
        f"[bold]Import Summary[/]\n\n"
        f"• Total scanned:      [bold]{summary.total_scanned}[/]\n"
        f"• {'Would import' if dry_run else 'Imported'}:          [bold green]{summary.imported}[/]\n"
        f"• Duplicates skipped: [bold yellow]{summary.duplicates_skipped}[/]\n"
        f"• Errors:             [bold red]{summary.errors}[/]"
    )
    console.print(Panel(summary_panel, border_style="green" if summary.errors == 0 else "yellow"))


@app.command("check")
def check_pdf(
    path: str = typer.Argument(
        ...,
        help="Path to a PDF file or folder to check for duplicates in Zotero.",
    ),
    quick_index: bool = typer.Option(
        False,
        "--quick-index",
        help="Only index Zotero attachments (skips bibliographic reference scan).",
    ),
):
    """
    Check if a PDF file or directory is already present in Zotero without importing.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        console.print(f"[bold red]Path not found:[/] {p}")
        raise typer.Exit(code=1)

    client = ZoteroClient()
    importer = PDFImporter(client=client)

    console.print("[cyan]Loading library duplicate index...[/]")
    importer.ensure_index(include_references=not quick_index)

    files_to_check = [p] if p.is_file() else list(discover_pdf_files(p, max_files=100))

    table = Table(title="Duplicate Verification", box=box.ROUNDED)
    table.add_column("File", style="cyan")
    table.add_column("Present in Zotero?", style="bold")
    table.add_column("Details", style="dim")

    for f in files_to_check:
        record = importer.import_single_pdf(f, dry_run=True)
        if record.status == "skipped_duplicate":
            table.add_row(f.name, "[yellow]YES (Duplicate)[/]", record.reason or "")
        else:
            table.add_row(f.name, "[green]NO (New)[/]", record.metadata.title if record.metadata else "")

    console.print(table)


@app.command("inspect")
def inspect_pdf(
    file_path: str = typer.Argument(..., help="Path to PDF file to inspect"),
    crossref: bool = typer.Option(True, "--crossref/--no-crossref"),
):
    """
    Inspect extracted metadata for a specific PDF file.
    """
    p = Path(file_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        console.print(f"[bold red]File not found:[/] {p}")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Extracting metadata for:[/] [yellow]{p.name}[/]")
    meta = extract_pdf_metadata(p, use_crossref=crossref)

    table = Table(box=box.ROUNDED, title=f"Metadata: {p.name}")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Title", meta.title)
    table.add_row("Item Type", meta.item_type)
    table.add_row("Source", meta.source)
    table.add_row("DOI", meta.doi or "-")
    table.add_row("Publication / Journal", meta.publication_title or "-")
    table.add_row("Date / Year", meta.date or "-")

    authors_str = "; ".join(
        f"{c.get('lastName', '')}, {c.get('firstName', '')}".strip(", ") or c.get("name", "")
        for c in meta.creators
    )
    table.add_row("Authors", authors_str or "-")

    if meta.volume:
        table.add_row("Volume", meta.volume)
    if meta.issue:
        table.add_row("Issue", meta.issue)
    if meta.pages:
        table.add_row("Pages", meta.pages)

    console.print(table)


if __name__ == "__main__":
    app()
