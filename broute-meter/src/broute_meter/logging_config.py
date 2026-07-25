"""アプリケーションログの設定と秘密情報のマスキング。"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from broute_meter.config import LoggingConfig

LOG_FILE_NAME = "broute-meter.log"
LOG_BACKUP_COUNT = 30
REDACTED_TEXT = "***"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Covers common forms such as:
#   B_ROUTE_ID=...
#   b-route-password: ...
#   id="..."
#   "password": "..."
#
# A generic ``id`` is matched only as a complete key, so ordinary words such as
# ``invalid`` and compound keys such as ``request_id`` are not altered.
_SENSITIVE_KEY_VALUE_PATTERN = re.compile(
    r"""
    (?P<prefix>
        (?<![\w-])
        (?P<key_quote>["']?)
        (?:
            b[\s_.-]*route[\s_.-]*(?:id|password)
            | id
            | password
            | passwd
            | pwd
        )
        (?P=key_quote)
        \s*(?:=|:)\s*
    )
    (?P<value>
        "(?:\\.|[^"\\])*"
        | '(?:\\.|[^'\\])*'
        | [^\s,;}\]&]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _normalized_secrets(secrets: Iterable[str]) -> tuple[str, ...]:
    """空値と重複を除き、部分一致時に長い秘密値を先に置く。"""

    return tuple(
        sorted(
            {secret for secret in secrets if isinstance(secret, str) and secret},
            key=len,
            reverse=True,
        )
    )


def _redact_key_value(match: re.Match[str]) -> str:
    """キー表現を保持し、値だけをマスクする。"""

    value = match.group("value")
    if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
        replacement = f"{value[0]}{REDACTED_TEXT}{value[0]}"
    else:
        replacement = REDACTED_TEXT
    return f"{match.group('prefix')}{replacement}"


def redact_sensitive_text(text: str, secrets: Iterable[str] = ()) -> str:
    """明示された秘密値と一般的なID・パスワード表現をマスクする。

    空の秘密値は無視する。空文字列を置換対象にすると文字間すべてへ
    マスク文字が挿入されるため、ログ自体が読めなくなるのを防ぐ。
    """

    redacted = text
    for secret in _normalized_secrets(secrets):
        redacted = redacted.replace(secret, REDACTED_TEXT)
    return _SENSITIVE_KEY_VALUE_PATTERN.sub(_redact_key_value, redacted)


class RedactingFormatter(logging.Formatter):
    """最終的に整形されたログ文字列から秘密情報を除去するFormatter。"""

    def __init__(
        self,
        *args: object,
        secrets: Iterable[str] = (),
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._secrets = _normalized_secrets(secrets)

    def format(self, record: logging.LogRecord) -> str:
        """``%s``引数、例外、スタック情報を含む最終出力をマスクする。"""

        return redact_sensitive_text(super().format(record), self._secrets)


def configure_logging(
    config: LoggingConfig,
    secrets: Iterable[str] = (),
    logger_name: str = "broute_meter",
) -> logging.Logger:
    """標準エラーと日次ローテーションファイルへ出力するloggerを構成する。

    同じloggerへ繰り返し適用してもhandlerは増殖しない。ログディレクトリ
    またはファイルを作成できない場合、例外は呼び出し元へ伝播する。
    """

    level_name = config.level.upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if level is None:
        raise ValueError(f"Unsupported logging level: {config.level}")

    log_directory = Path(config.directory)
    log_directory.mkdir(parents=True, exist_ok=True)

    formatter = RedactingFormatter(
        _LOG_FORMAT,
        datefmt=_LOG_DATE_FORMAT,
        secrets=secrets,
    )

    file_handler = TimedRotatingFileHandler(
        log_directory / LOG_FILE_NAME,
        when="midnight",
        interval=1,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    logger = logging.getLogger(logger_name)
    for existing_handler in tuple(logger.handlers):
        logger.removeHandler(existing_handler)
        existing_handler.close()

    logger.setLevel(level)
    logger.propagate = False
    logger.disabled = False
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger
