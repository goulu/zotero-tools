from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console
from typer.testing import CliRunner

import pdf_importer.cli as cli
from pdf_importer.extractor import ExtractedMetadata
from pdf_importer.importer import ImportSummary, ItemImportRecord


def test_import_cli_prints_and_logs_errors_as_they_arrive(tmp_path, monkeypatch):
    target_dir = tmp_path / "articles"
    target_dir.mkdir()
    failed_pdf = target_dir / "failed.pdf"
    output = StringIO()

    class FakeClient:
        def ping(self):
            return {"version": "test"}

    class FakeImporter:
        def __init__(self, client, config):
            self.index = SimpleNamespace(
                filenames=set(),
                dois=set(),
                titles=set(),
            )

        def ensure_index(self, **kwargs):
            pass

        def import_directory(self, directory, on_file_processed, **kwargs):
            record = ItemImportRecord(
                file_path=failed_pdf,
                status="error",
                reason="simulated failure",
                metadata=ExtractedMetadata(title="Failed"),
            )
            on_file_processed(record, 1)
            return ImportSummary(
                total_scanned=1,
                errors=1,
                records=[record],
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "ZoteroClient", FakeClient)
    monkeypatch.setattr(cli, "PDFImporter", FakeImporter)
    monkeypatch.setattr(
        cli,
        "console",
        Console(file=output, force_terminal=False),
    )

    result = CliRunner().invoke(
        cli.app,
        ["import", str(target_dir), "--dry-run", "--max", "1"],
    )

    console_output = output.getvalue()
    assert result.exit_code == 0, (
        result.output,
        repr(result.exception),
        output.getvalue(),
    )
    assert console_output.index("simulated failure") < console_output.index(
        "Import Summary"
    )
    log_content = (tmp_path / "pdf-importer-errors.log").read_text(
        encoding="utf-8"
    )
    assert str(failed_pdf) in log_content
    assert "simulated failure" in log_content