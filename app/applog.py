"""Rotating application log for the workstation process."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.models import LoggingSettings

LOGGER_NAME = "dicommunication"
LOG_FILE_NAME = "dicommunication.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
VIEWER_MAX_BYTES = 256 * 1024
SKIP_HTTP_PATHS = ("/static", "/health", "/logs/live", "/favicon")

log = logging.getLogger(LOGGER_NAME)

_file_handler: RotatingFileHandler | None = None


def log_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / LOG_FILE_NAME


def configure(data_dir: Path | str, settings: LoggingSettings | None = None) -> Path:
    """Install a rotating file handler (and stderr) for this process."""
    global _file_handler
    settings = settings or LoggingSettings()
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.level, logging.INFO)
    formatter = logging.Formatter(FORMAT, DATEFMT)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        path,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    _file_handler = file_handler

    stream = logging.StreamHandler()
    stream.setLevel(level)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    extra = logging.DEBUG if settings.level == "DEBUG" else logging.WARNING
    logging.getLogger("pynetdicom").setLevel(extra)
    logging.getLogger("uvicorn.error").setLevel(level)

    return path


def clear_log(data_dir: Path | str, settings: LoggingSettings) -> Path:
    path = configure(data_dir, settings)
    if _file_handler is not None:
        _file_handler.close()
    path.write_text("", encoding="utf-8")
    configure(data_dir, settings)
    log.info("Log file cleared")
    return path


def read_tail(path: Path | str, max_bytes: int = VIEWER_MAX_BYTES) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    size = file_path.stat().st_size
    with file_path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        payload = handle.read()
    return payload.decode("utf-8", errors="replace")


def line_level(line: str) -> str:
    for level in ("CRITICAL", "ERROR", "WARNING", "DEBUG", "INFO"):
        if f" {level} " in line:
            return level
    return "INFO"


def viewer_lines(text: str) -> list[dict[str, str]]:
    lines = text.splitlines() or ([text] if text else [])
    return [{"level": line_level(line), "text": line} for line in lines]


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def should_skip_http_log(path: str) -> bool:
    return path.startswith(SKIP_HTTP_PATHS)
