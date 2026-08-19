from __future__ import annotations

from pathlib import Path

from app.applog import configure, format_size, line_level, log, log_path, read_tail, viewer_lines
from app.models import LoggingSettings


def test_logging_defaults() -> None:
    settings = LoggingSettings()
    assert settings.level == "INFO"
    assert settings.max_megabytes == 2
    assert settings.backup_count == 3


def test_configure_writes_and_tails(tmp_path) -> None:
    settings = LoggingSettings(level="INFO", max_bytes=1024 * 1024, backup_count=2)
    path = configure(tmp_path, settings)
    assert path == log_path(tmp_path)
    log.info("hello from tests")
    for handler in log.handlers:
        handler.flush()
    text = read_tail(path)
    assert "hello from tests" in text
    assert "INFO" in text
    lines = viewer_lines(text)
    assert lines[-1]["level"] == "INFO"


def test_read_tail_missing_file(tmp_path) -> None:
    assert read_tail(tmp_path / "missing.log") == ""


def test_line_level_and_size_label() -> None:
    assert line_level("2026-08-19 12:00:00 WARNING dicommunication: nope") == "WARNING"
    assert line_level("plain text") == "INFO"
    assert format_size(512) == "512 B"
    assert format_size(2048).endswith("KB")


def test_logs_page_and_settings_form(client, store) -> None:
    page = client.get("/logs")
    assert page.status_code == 200
    assert b"Amount and file size" in page.content
    assert b"Log view" in page.content
    assert b'name="level"' in page.content
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    assert "color-scheme: dark" in css
    assert "html.light-mode { color-scheme: light; }" in css
    assert ".choice-menu" in css
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "enhanceSelectMenus" in js
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert 'meta[name="color-scheme"]' in js
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    assert 'name="color-scheme" content="dark"' in html
    assert b'id="log-view"' in page.content
    assert b'href="/logs"' in client.get("/").content

    saved = client.post(
        "/logs",
        data={"level": "DEBUG", "max_megabytes": "5", "backup_count": "4"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b"Logging settings saved" in saved.content
    config = store.load()
    assert config.logging.level == "DEBUG"
    assert config.logging.max_bytes == 5 * 1024 * 1024
    assert config.logging.backup_count == 4
    assert b"Logging set to DEBUG" in client.get("/logs/live").content


def test_reject_oversized_log_file(client) -> None:
    response = client.post(
        "/logs",
        data={"level": "INFO", "max_megabytes": "999", "backup_count": "3"},
    )
    assert response.status_code == 400


def test_logging_api_and_tool_run(client, remote) -> None:
    listed = client.get("/api/logging").json()
    assert listed["level"] == "INFO"
    updated = client.put("/api/logging", json={"level": "INFO", "max_bytes": 1048576, "backup_count": 2})
    assert updated.status_code == 200
    assert updated.json()["backup_count"] == 2

    ping = client.post("/api/tools/ping/run", json={"remote_id": remote.id})
    assert ping.status_code == 200
    body = client.get("/api/logs").json()
    assert "Run ping" in body["text"]
    assert body["size"] > 0
