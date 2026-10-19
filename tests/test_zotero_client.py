from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_cleaner.client import ZoteroClient, ZoteroClientError
from zotero_cleaner.config import ZoteroConfig
from pdf_importer.cli import _append_import_error


@pytest.mark.parametrize("operation", ["create", "update"])
def test_write_authorization_is_requested_only_once(
    monkeypatch,
    capsys,
    operation,
):
    config = ZoteroConfig(
        api_url="http://localhost:23119/api",
        user_id="0",
        api_key=None,
        timeout=1.0,
    )
    client = ZoteroClient(config)
    client.server_id = "test-server"
    unauthorized = SimpleNamespace(
        status_code=401,
        text="Unauthorized",
        headers={},
        content=b"",
    )
    request = Mock(return_value=unauthorized)
    monkeypatch.setattr(client.client, "post" if operation == "create" else "patch", request)
    authorization = Mock(return_value="test-key")
    monkeypatch.setattr(client, "request_authorization", authorization)

    def write():
        if operation == "create":
            client.create_items([{}], current_version=1)
        else:
            client.update_item_data("ITEM1", 1, {"title": "Updated"})

    with pytest.raises(ZoteroClientError):
        write()
    with pytest.raises(ZoteroClientError, match="stopping to avoid repeated"):
        write()

    assert authorization.call_count == 1
    assert request.call_count == 3
    assert capsys.readouterr().out.count("Waiting for manual authorization") == 1
    client.close()


def test_import_error_is_written_to_log(tmp_path):
    log_path = tmp_path / "pdf-importer-errors.log"

    _append_import_error(log_path, tmp_path / "paper.pdf", "PDF failed\nreason")

    log_content = log_path.read_text(encoding="utf-8")
    assert str(tmp_path / "paper.pdf") in log_content
    assert "PDF failed reason" in log_content