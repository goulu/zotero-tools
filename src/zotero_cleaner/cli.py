"""Command line interface for Zotero Cleaner."""

from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from zotero_cleaner.client import ZoteroClient, ZoteroClientError
from zotero_cleaner.config import ZoteroConfig
from zotero_cleaner.cleaners import (
    TitleCleaner,
    DuplicateDetector,
    CreatorCleaner,
    TagCleaner,
    AttachmentCleaner,
)

app = typer.Typer(
    name="zotero-cleaner",
    help="Audit and cleaning tool for Zotero libraries via the local HTTP API.",
    add_completion=False,
)
console = Console()


def get_client(url: Optional[str] = None, user_id: Optional[str] = None) -> ZoteroClient:
    cfg = ZoteroConfig()
    if url:
        cfg.api_url = url
    if user_id:
        cfg.user_id = user_id
    return ZoteroClient(cfg)


@app.command()
def ping(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Check connectivity with the local Zotero instance."""
    client = get_client(url=url)
    with console.status("[bold green]Connecting to Zotero..."):
        try:
            res = client.ping()
            console.print(
                Panel.fit(
                    f"[bold green]✔ Zotero is online and accessible![/bold green]\n\n"
                    f"[cyan]API URL:[/cyan] {res.get('url', client.config.api_url)}\n"
                    f"[cyan]Zotero Version:[/cyan] {res.get('version', 'unknown')}\n"
                    f"[cyan]Connector API:[/cyan] {res.get('connector_api', 'v3')}",
                    title="Connection Status",
                    border_style="green",
                )
            )
        except ZoteroClientError as e:
            console.print(
                Panel.fit(
                    f"[bold red]❌ Unable to reach Zotero![/bold red]\n\n{e}\n\n"
                    f"[yellow]Please verify that Zotero is running on this computer.[/yellow]",
                    title="Connection Error",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)


def ensure_authorized(client: ZoteroClient):
    """Ensure the client has a valid write authorization key."""
    if not client.config.api_key:
        console.print(
            Panel.fit(
                "[bold yellow]🔐 Write Authorization Required by Zotero[/bold yellow]\n\n"
                "A confirmation dialog will appear in your Zotero application.\n"
                "Click [bold green]'Always Allow'[/bold green] (or 'Allow') to confirm access.",
                title="Zotero Authorization",
                border_style="yellow",
            )
        )
        try:
            client.request_authorization()
            console.print("[bold green]✔ Authorization granted and saved to .env![/bold green]\n")
        except ZoteroClientError as e:
            console.print(f"[bold red]❌ Authorization failed:[/bold red] {e}")
            raise typer.Exit(code=1)


@app.command()
def auth(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Request write authorization from Zotero (generates a local API key)."""
    client = get_client(url=url)
    ensure_authorized(client)


@app.command()
def stats(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Display general statistics for the Zotero library."""
    client = get_client(url=url)
    try:
        with console.status("[bold blue]Retrieving metrics..."):
            total_items = client.get_total_items(top_level=False)
            top_level = client.get_total_items(top_level=True)
            attachments_and_notes = total_items - top_level

        table = Table(title="Zotero Library Statistics", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right", style="bold cyan")

        table.add_row("Total elements (including files & notes)", str(total_items))
        table.add_row("Top-level items (references)", str(top_level))
        table.add_row("Attachments and notes", str(attachments_and_notes))

        console.print(table)
    except ZoteroClientError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def duplicates(
    limit: int = typer.Option(500, "--limit", "-n", help="Max references to analyze (0 = all)"),
    tag_mode: bool = typer.Option(False, "--tag", help="Add tag 'zotero-cleaner:duplicate' to detected duplicates"),
    apply: bool = typer.Option(False, "--apply", help="Apply changes (default: dry-run / simulation)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Search for potential duplicate references (by DOI, ISBN, and Title + Author)."""
    client = get_client(url=url)
    max_items = None if limit <= 0 else limit

    items = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Loading references...", total=max_items or 100)

        def update_prog(fetched, total):
            progress.update(task, completed=fetched, total=total)

        try:
            for it in client.iter_all_items(batch_size=100, max_items=max_items, top_level=True, on_batch=update_prog):
                items.append(it)
        except ZoteroClientError as e:
            console.print(f"[bold red]Error during loading:[/bold red] {e}")
            raise typer.Exit(code=1)

    console.print(f"[green]✔ {len(items)} items loaded.[/green]")
    detector = DuplicateDetector()
    groups = detector.find_duplicates(items)

    if not groups:
        console.print("[bold green]Great! No duplicate references detected in the analyzed sample.[/bold green]")
        return

    table = Table(title=f"Detected Duplicates ({len(groups)} group(s))", show_header=True, header_style="bold yellow")
    table.add_column("Criterion", style="cyan")
    table.add_column("Key / Value", style="dim")
    table.add_column("Count", justify="center", style="bold red")
    table.add_column("Recommended Master Item", style="green")
    table.add_column("Identified Duplicate(s)", style="yellow")

    for grp in groups:
        master_title = (grp.master_item.title[:45] + "...") if grp.master_item and len(grp.master_item.title) > 45 else (grp.master_item.title if grp.master_item else "")
        dups_desc = ", ".join([f"[{it.key}] {it.title[:30]}" for it in grp.duplicate_items])
        table.add_row(
            grp.criterion,
            grp.key_value,
            str(len(grp.items)),
            f"[{grp.master_item.key if grp.master_item else ''}] {master_title}",
            dups_desc[:80] + ("..." if len(dups_desc) > 80 else ""),
        )

    console.print(table)

    if not apply:
        console.print("\n[bold yellow]ℹ DRY-RUN mode.[/bold yellow] No changes were made.")
        console.print("To tag duplicate references in Zotero, re-run with: [cyan]zotero-cleaner duplicates --tag --apply[/cyan]")
        return

    ensure_authorized(client)

    if tag_mode and apply:
        confirm = typer.confirm(f"Add tag 'zotero-cleaner:duplicate' to {sum(len(g.duplicate_items) for g in groups)} duplicate(s)?")
        if not confirm:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        tagged_count = 0
        for grp in groups:
            for dup in grp.duplicate_items:
                current_tags = [t.model_dump() for t in dup.data.tags]
                if not any(t.get("tag") == "zotero-cleaner:duplicate" for t in current_tags):
                    current_tags.append({"tag": "zotero-cleaner:duplicate", "type": 0})
                    try:
                        client.update_item_data(dup.key, dup.version, {"tags": current_tags})
                        tagged_count += 1
                    except Exception as e:
                        console.print(f"[red]Error tagging {dup.key}: {e}[/red]")

        console.print(f"[bold green]✔ {tagged_count} duplicate(s) successfully tagged![/bold green]")


@app.command()
def clean_titles(
    limit: int = typer.Option(200, "--limit", "-n", help="Max items to analyze (0 = all)"),
    apply: bool = typer.Option(False, "--apply", help="Apply modifications (default: dry-run / simulation)"),
    fix_all_caps: bool = typer.Option(True, "--all-caps/--no-all-caps", help="Convert ALL-CAPS titles to Sentence case"),
    strip_trailing_dot: bool = typer.Option(True, "--strip-dot/--no-strip-dot", help="Remove unnecessary trailing periods"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Audit and clean title formatting (ALL CAPS, HTML tags, whitespace, trailing periods)."""
    client = get_client(url=url)
    cleaner = TitleCleaner(fix_all_caps=fix_all_caps, strip_trailing_dot=strip_trailing_dot)
    max_items = None if limit <= 0 else limit

    issues = []
    with console.status("[bold blue]Analyzing titles..."):
        try:
            for it in client.iter_all_items(batch_size=100, max_items=max_items, top_level=True):
                item_issues = cleaner.analyze(it)
                issues.extend(item_issues)
        except ZoteroClientError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    if not issues:
        console.print("[bold green]✔ All analyzed titles are clean![/bold green]")
        return

    table = Table(title=f"Proposed Title Cleanups ({len(issues)} found)", show_header=True)
    table.add_column("Key", style="cyan", width=10)
    table.add_column("Reason(s)", style="magenta", width=30)
    table.add_column("Original Title", style="red")
    table.add_column("Corrected Title", style="green")

    for iss in issues[:30]:
        table.add_row(iss.item_key, iss.description, iss.original_value, iss.proposed_value)

    console.print(table)
    if len(issues) > 30:
        console.print(f"[dim]... and {len(issues) - 30} other proposal(s).[/dim]")

    if not apply:
        console.print("\n[bold yellow]ℹ DRY-RUN mode.[/bold yellow]")
        console.print("To apply these changes to Zotero, run: [cyan]zotero-cleaner clean-titles --apply[/cyan]")
        return

    ensure_authorized(client)

    confirm = typer.confirm(f"Confirm applying these {len(issues)} title modifications?")
    if not confirm:
        console.print("[yellow]Operation cancelled.[/yellow]")
        return

    applied = 0
    with Progress() as prog:
        task = prog.add_task("Updating in Zotero...", total=len(issues))
        for iss in issues:
            try:
                client.update_item_data(iss.item_key, iss.item_version, iss.patch_data)
                applied += 1
            except Exception as e:
                console.print(f"[red]Error on {iss.item_key}: {e}[/red]")
            prog.advance(task)

    console.print(f"[bold green]✔ {applied} title(s) successfully updated in Zotero![/bold green]")


@app.command()
def clean_tags(
    limit: int = typer.Option(200, "--limit", "-n", help="Max items to analyze (0 = all)"),
    apply: bool = typer.Option(False, "--apply", help="Apply modifications (default: dry-run / simulation)"),
    remove_automatic: bool = typer.Option(True, "--automatic/--no-automatic", help="Remove automatically imported tags"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Audit and remove unwanted or automatic tags."""
    client = get_client(url=url)
    cleaner = TagCleaner(remove_automatic=remove_automatic)
    max_items = None if limit <= 0 else limit

    issues = []
    with console.status("[bold blue]Analyzing tags..."):
        try:
            for it in client.iter_all_items(batch_size=100, max_items=max_items, top_level=True):
                item_issues = cleaner.analyze(it)
                issues.extend(item_issues)
        except ZoteroClientError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    if not issues:
        console.print("[bold green]✔ No unwanted tags found![/bold green]")
        return

    table = Table(title=f"Tag Cleanup ({len(issues)} reference(s) affected)")
    table.add_column("Key", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Action", style="yellow")

    for iss in issues[:25]:
        title = (iss.item_title[:40] + "...") if len(iss.item_title) > 40 else iss.item_title
        table.add_row(iss.item_key, title, iss.description)

    console.print(table)
    if len(issues) > 25:
        console.print(f"[dim]... and {len(issues) - 25} other reference(s).[/dim]")

    if not apply:
        console.print("\n[bold yellow]ℹ DRY-RUN mode.[/bold yellow]")
        console.print("To apply tag cleanup, run: [cyan]zotero-cleaner clean-tags --apply[/cyan]")
        return

    ensure_authorized(client)

    confirm = typer.confirm(f"Confirm cleaning tags on {len(issues)} reference(s)?")
    if not confirm:
        console.print("[yellow]Operation cancelled.[/yellow]")
        return

    applied = 0
    for iss in issues:
        try:
            client.update_item_data(iss.item_key, iss.item_version, iss.patch_data)
            applied += 1
        except Exception as e:
            console.print(f"[red]Error on {iss.item_key}: {e}[/red]")

    console.print(f"[bold green]✔ {applied} reference(s) updated![/bold green]")


@app.command()
def clean_attachments(
    limit: int = typer.Option(1000, "--limit", "-n", help="Max attachments to analyze (0 = all)"),
    scope: str = typer.Option("parent", "--scope", "-s", help="Scope: 'parent' (same reference) or 'global' (entire library by MD5)"),
    apply: bool = typer.Option(False, "--apply", help="Move duplicates to Zotero's Trash (default: dry-run)"),
    tag_mode: bool = typer.Option(False, "--tag", help="Add tag 'zotero-cleaner:duplicate-attachment' instead of deleting"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """Search and remove duplicate attachments (redundant PDF files, snapshots, or web links)."""
    client = get_client(url=url)
    max_items = None if limit <= 0 else limit
    cleaner = AttachmentCleaner(scope=scope)

    attachments = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Loading attachments...", total=max_items or 100)

        def update_prog(fetched, total):
            progress.update(task, completed=fetched, total=total)

        try:
            for it in client.iter_all_items(
                batch_size=100,
                max_items=max_items,
                top_level=False,
                item_type="attachment",
                on_batch=update_prog,
            ):
                attachments.append(it)
        except ZoteroClientError as e:
            console.print(f"[bold red]Error loading attachments:[/bold red] {e}")
            raise typer.Exit(code=1)

    console.print(f"[green]✔ {len(attachments)} attachments analyzed.[/green]")
    groups = cleaner.find_duplicates(attachments)

    if not groups:
        console.print("[bold green]✔ No duplicate attachments detected![/bold green]")
        return

    total_dups = sum(len(g.duplicate_items) for g in groups)
    table = Table(
        title=f"Duplicate Attachments ({len(groups)} group(s), {total_dups} duplicate(s))",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Parent", style="cyan", width=12)
    table.add_column("Criterion", style="magenta", width=24)
    table.add_column("Preserved Attachment (Master)", style="green")
    table.add_column("Duplicate Attachment(s) to Remove", style="red")

    for grp in groups[:30]:
        parent_str = grp.parent_key or "Global"
        master = grp.master_item
        master_info = f"[{master.key}] {master.filename or master.title or 'Untitled'}" if master else ""
        dups_info = ", ".join([f"[{d.key}] {d.filename or d.title or 'Untitled'}" for d in grp.duplicate_items])
        table.add_row(
            parent_str,
            f"{grp.criterion} ({grp.key_value})",
            master_info,
            dups_info[:80] + ("..." if len(dups_info) > 80 else ""),
        )

    console.print(table)
    if len(groups) > 30:
        console.print(f"[dim]... and {len(groups) - 30} other group(s).[/dim]")

    if not apply:
        console.print("\n[bold yellow]ℹ DRY-RUN mode.[/bold yellow] No attachments were removed.")
        console.print("To move these duplicates to Zotero's Trash: [cyan]zotero-cleaner clean-attachments --apply[/cyan]")
        return

    ensure_authorized(client)

    if tag_mode:
        confirm = typer.confirm(f"Tag the {total_dups} duplicate(s) with 'zotero-cleaner:duplicate-attachment'?")
        if not confirm:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        tagged = 0
        for grp in groups:
            for dup in grp.duplicate_items:
                current_tags = [t.model_dump() for t in dup.data.tags]
                if not any(t.get("tag") == "zotero-cleaner:duplicate-attachment" for t in current_tags):
                    current_tags.append({"tag": "zotero-cleaner:duplicate-attachment", "type": 0})
                    try:
                        client.update_item_data(dup.key, dup.version, {"tags": current_tags})
                        tagged += 1
                    except Exception as e:
                        console.print(f"[red]Error tagging {dup.key}: {e}[/red]")
        console.print(f"[bold green]✔ {tagged} attachment(s) tagged![/bold green]")
        return

    confirm = typer.confirm(
        f"Confirm moving these {total_dups} duplicate attachment(s) to Zotero's Trash?"
    )
    if not confirm:
        console.print("[yellow]Operation cancelled.[/yellow]")
        return

    deleted = 0
    with Progress() as prog:
        task = prog.add_task("Moving duplicate attachments to Trash...", total=total_dups)
        for grp in groups:
            for dup in grp.duplicate_items:
                try:
                    client.delete_item(dup.key, dup.version)
                    deleted += 1
                except Exception as e:
                    console.print(f"[red]Error deleting {dup.key}: {e}[/red]")
                prog.advance(task)

    console.print(f"[bold green]✔ {deleted} duplicate attachment(s) moved to Zotero's Trash![/bold green]")


@app.command()
def audit(
    limit: int = typer.Option(100, "--limit", "-n", help="Sample of references to audit (0 = all)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Local API URL"),
):
    """General health audit of the Zotero library."""
    client = get_client(url=url)
    max_items = None if limit <= 0 else limit

    title_cleaner = TitleCleaner()
    creator_cleaner = CreatorCleaner()
    tag_cleaner = TagCleaner()
    dup_detector = DuplicateDetector()
    att_cleaner = AttachmentCleaner()

    items = []
    title_issues = []
    creator_issues = []
    tag_issues = []

    with console.status(f"[bold cyan]Auditing references ({max_items or 'all'} items sample)..."):
        try:
            for it in client.iter_all_items(batch_size=100, max_items=max_items, top_level=True):
                items.append(it)
                title_issues.extend(title_cleaner.analyze(it))
                creator_issues.extend(creator_cleaner.analyze(it))
                tag_issues.extend(tag_cleaner.analyze(it))
        except ZoteroClientError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            raise typer.Exit(code=1)

    dup_groups = dup_detector.find_duplicates(items)

    # Check a sample of attachments
    att_issues_count = 0
    try:
        sample_atts = []
        for att in client.iter_all_items(batch_size=100, max_items=max_items or 200, top_level=False, item_type="attachment"):
            sample_atts.append(att)
        att_groups = att_cleaner.find_duplicates(sample_atts)
        att_issues_count = sum(len(g.duplicate_items) for g in att_groups)
    except Exception:
        pass

    table = Table(title="Zotero Health Audit Report", show_header=True, header_style="bold blue")
    table.add_column("Checked Domain", style="cyan")
    table.add_column("Detected Issues", justify="right", style="bold red")
    table.add_column("Recommended Action", style="green")

    table.add_row(
        "Titles (ALL-CAPS, HTML, spaces)",
        str(len(title_issues)),
        "zotero-cleaner clean-titles",
    )
    table.add_row(
        "Authors (Casing, Last/First fields, et al.)",
        str(len(creator_issues)),
        "Review author names",
    )
    table.add_row(
        "Tags (Automatic / imported tags)",
        str(len(tag_issues)),
        "zotero-cleaner clean-tags",
    )
    table.add_row(
        "Duplicate references (DOI, ISBN, Title)",
        f"{len(dup_groups)} group(s)",
        "zotero-cleaner duplicates",
    )
    table.add_row(
        "Duplicate attachments (PDFs, snapshots)",
        f"{att_issues_count} duplicate(s)",
        "zotero-cleaner clean-attachments",
    )

    console.print(table)
    console.print(f"\n[bold green]Sample analyzed:[/bold green] {len(items)} top-level references.")


if __name__ == "__main__":
    app()
