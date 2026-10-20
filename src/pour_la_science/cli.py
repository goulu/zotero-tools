"""CLI for importing Pour la Science issue articles into Zotero."""

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from zotero_cleaner.client import ZoteroClient

from .archives import parse_issue_page
from .client import PourLaScienceClient
from .zotero_importer import PourLaScienceZoteroImporter


app = typer.Typer(
    name="pour-la-science",
    help="Find and import articles from Pour la Science magazine issues.",
    add_completion=False,
)
console = Console()


@app.command("import-issue")
def import_issue(
    issue_url: str = typer.Argument(
        ...,
        help="URL of a Pour la Science issue page.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Write new journal articles to Zotero (default is a dry-run).",
    ),
):
    site_client = PourLaScienceClient()
    zotero_client = ZoteroClient()
    try:
        console.print("[cyan]Connecting to Pour la Science...[/]")
        site_client.login()
        response = site_client.get_issue_page(issue_url)
        issue = parse_issue_page(response.text, str(response.url))
        if not issue.articles:
            console.print(
                "[bold red]No issue articles were found on this page.[/]"
            )
            raise typer.Exit(code=1)

        console.print(Panel(
            f"[bold]{issue.title or 'Pour la Science'}[/]\n"
            f"Issue: {issue.issue_number or 'unknown'} | "
            f"Articles: {len(issue.articles)} | "
            f"Mode: {'IMPORT' if apply else 'DRY-RUN'}",
            border_style="cyan",
        ))

        try:
            ping = zotero_client.ping()
            console.print(
                "[green]Connected to Zotero[/] "
                f"(v{ping.get('version', 'unknown')})"
            )
        except Exception as error:
            console.print(f"[bold red]Cannot connect to Zotero:[/] {error}")
            raise typer.Exit(code=1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "[cyan]Checking Zotero for existing articles...",
                total=None,
            )
            summary = PourLaScienceZoteroImporter(zotero_client).import_issue(
                issue,
                dry_run=not apply,
            )
            progress.update(task, description="[green]Zotero check complete")

        table = Table(box=box.SIMPLE, expand=True)
        table.add_column("Status", style="bold", width=12)
        table.add_column("Article", ratio=3)
        table.add_column("Authors", ratio=2)
        table.add_column("Details", ratio=2)
        status_styles = {
            "added": "green",
            "would_add": "yellow",
            "duplicate": "dim",
            "error": "red",
        }
        status_labels = {
            "added": "ADDED",
            "would_add": "WOULD ADD",
            "duplicate": "DUPLICATE",
            "error": "ERROR",
        }
        for result in summary.results:
            table.add_row(
                (
                    f"[{status_styles[result.status]}]"
                    f"{status_labels[result.status]}[/]"
                ),
                result.article.title,
                "; ".join(result.article.authors),
                result.detail,
            )
        if summary.results:
            console.print(table)

        summary_text = (
            f"Articles parsed: {len(issue.articles)}\n"
            f"{'Would add' if not apply else 'Added'}: "
            f"{summary.would_add if not apply else summary.added}\n"
            f"Already present: {summary.duplicates}\n"
            f"Errors: {summary.errors}"
        )
        console.print(Panel(
            summary_text,
            border_style="red" if summary.errors else "green",
        ))
        if summary.errors:
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        console.print(f"[bold red]Pour la Science import failed:[/] {error}")
        raise typer.Exit(code=1)
    finally:
        site_client.close()
        zotero_client.close()


if __name__ == "__main__":
    app()
