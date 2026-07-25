"""ログ設定と秘密情報マスキングの単体テスト。"""

from __future__ import annotations

import io
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from broute_meter.config import LoggingConfig
from broute_meter.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    REDACTED_TEXT,
    RedactingFormatter,
    configure_logging,
    redact_sensitive_text,
)


def _close_handlers(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_redact_sensitive_text_masks_explicit_secrets_and_ignores_empty_values() -> None:
    text = "token=plain-token unrelated text"

    redacted = redact_sensitive_text(text, ("plain-token", "", ""))

    assert redacted == f"token={REDACTED_TEXT} unrelated text"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("B_ROUTE_ID=0123456789", f"B_ROUTE_ID={REDACTED_TEXT}"),
        ("b-route-password: secret", f"b-route-password: {REDACTED_TEXT}"),
        ("id='meter-id'", f"id='{REDACTED_TEXT}'"),
        (
            '{"password": "do-not-log"}',
            f'{{"password": "{REDACTED_TEXT}"}}',
        ),
        ("passwd=hunter2; status=failed", f"passwd={REDACTED_TEXT}; status=failed"),
    ],
)
def test_redact_sensitive_text_masks_common_key_value_forms(
    text: str,
    expected: str,
) -> None:
    assert redact_sensitive_text(text) == expected


def test_redacting_formatter_masks_percent_arguments_and_exception_text() -> None:
    secret = "argument-secret"
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(RedactingFormatter("%(message)s", secrets=(secret,)))
    logger = logging.getLogger("test.redacting-formatter")
    _close_handlers(logger)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    try:
        logger.error("request credential=%s", secret)
        try:
            raise ValueError(f"password={secret}")
        except ValueError:
            logger.exception("request failed")
    finally:
        _close_handlers(logger)

    formatted = output.getvalue()
    assert secret not in formatted
    assert f"credential={REDACTED_TEXT}" in formatted
    assert f"password={REDACTED_TEXT}" in formatted
    assert "ValueError" in formatted


def test_configure_logging_adds_stderr_and_daily_rotating_file_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    logger_name = "test.configure-logging.handlers"
    logger = configure_logging(
        LoggingConfig(level="DEBUG", directory=tmp_path),
        logger_name=logger_name,
    )

    try:
        assert logger.level == logging.DEBUG
        assert logger.propagate is False
        assert len(logger.handlers) == 2

        plain_stream_handlers = [
            handler
            for handler in logger.handlers
            if type(handler) is logging.StreamHandler
        ]
        rotating_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, TimedRotatingFileHandler)
        ]
        assert len(plain_stream_handlers) == 1
        assert plain_stream_handlers[0].stream is stderr
        assert len(rotating_handlers) == 1

        rotating_handler = rotating_handlers[0]
        assert rotating_handler.when == "MIDNIGHT"
        assert rotating_handler.interval == 24 * 60 * 60
        assert rotating_handler.backupCount == LOG_BACKUP_COUNT
        assert rotating_handler.encoding.lower().replace("-", "") == "utf8"
        assert Path(rotating_handler.baseFilename) == tmp_path / LOG_FILE_NAME
    finally:
        _close_handlers(logger)


def test_configure_logging_replaces_handlers_instead_of_duplicating_them(
    tmp_path: Path,
) -> None:
    logger_name = "test.configure-logging.repeat"
    config = LoggingConfig(level="INFO", directory=tmp_path)
    first = configure_logging(config, logger_name=logger_name)
    first_handlers = tuple(first.handlers)

    second = configure_logging(config, logger_name=logger_name)

    try:
        assert first is second
        assert len(second.handlers) == 2
        assert not any(handler in second.handlers for handler in first_handlers)
    finally:
        _close_handlers(second)


def test_configure_logging_redacts_secrets_in_stderr_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "top-secret-value"
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    logger = configure_logging(
        LoggingConfig(level="INFO", directory=tmp_path),
        secrets=(secret,),
        logger_name="test.configure-logging.redaction",
    )

    try:
        logger.warning("B_ROUTE_ID=%s", secret)
        for handler in logger.handlers:
            handler.flush()
    finally:
        _close_handlers(logger)

    file_text = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert secret not in stderr.getvalue()
    assert secret not in file_text
    assert f"B_ROUTE_ID={REDACTED_TEXT}" in stderr.getvalue()
    assert f"B_ROUTE_ID={REDACTED_TEXT}" in file_text


def test_configure_logging_propagates_directory_creation_failure(tmp_path: Path) -> None:
    file_instead_of_directory = tmp_path / "not-a-directory"
    file_instead_of_directory.write_text("occupied", encoding="utf-8")

    with pytest.raises(FileExistsError):
        configure_logging(
            LoggingConfig(level="INFO", directory=file_instead_of_directory),
            logger_name="test.configure-logging.directory-error",
        )
