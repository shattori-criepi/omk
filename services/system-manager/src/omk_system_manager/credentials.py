"""B-route credential validation and durable, atomic storage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Final

import yaml

CREDENTIALS_MODE: Final = 0o600


class CredentialValidationError(ValueError):
    """Raised without including a supplied credential value."""


class CredentialWriteError(RuntimeError):
    """Raised without including credential contents or filesystem payloads."""


def validate_broute_credentials(identifier: object, password: object) -> tuple[str, str]:
    """Validate the RS-WSUHA-P credential tokens without echoing them."""

    return (
        _validate_token(identifier, expected_bytes=32, field_name="id"),
        _validate_token(password, expected_bytes=12, field_name="password"),
    )


def mask_broute_id(identifier: str | None) -> str | None:
    """Return a display-safe B-route ID representation."""

    if not identifier:
        return None
    if len(identifier) <= 8:
        return "*" * len(identifier)
    return f"{identifier[:4]}{'*' * (len(identifier) - 8)}{identifier[-4:]}"


def credential_status(path: Path) -> tuple[bool, str | None, bool]:
    """Return only display-safe state; malformed files are treated as unconfigured."""

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        b_route = loaded.get("b_route") if isinstance(loaded, dict) else None
        identifier = b_route.get("id") if isinstance(b_route, dict) else None
        password = b_route.get("password") if isinstance(b_route, dict) else None
        identifier, password = validate_broute_credentials(identifier, password)
    except (OSError, yaml.YAMLError, CredentialValidationError, AttributeError, TypeError):
        return False, None, False
    return True, mask_broute_id(identifier), bool(password)


def write_credentials_atomically(path: Path, identifier: str, password: str) -> None:
    """Replace credentials with a mode-0600 file, preserving the old file on write failure."""

    # Validate here too so callers cannot accidentally persist invalid values.
    identifier, password = validate_broute_credentials(identifier, password)
    directory = path.parent
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".credentials.", dir=directory)
        os.fchmod(descriptor, CREDENTIALS_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = None
            yaml.safe_dump(
                {"b_route": {"id": identifier, "password": password}},
                output,
                allow_unicode=False,
                default_flow_style=False,
                sort_keys=False,
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        # mkstemp plus fchmod already make the replacement 0600. Keep this
        # explicit to repair a surprising filesystem implementation.
        os.chmod(path, CREDENTIALS_MODE)
        _fsync_directory(directory)
    except CredentialValidationError:
        raise
    except OSError as error:
        raise CredentialWriteError("認証情報ファイルを安全に保存できませんでした") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _validate_token(value: object, *, expected_bytes: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise CredentialValidationError(f"b_route.{field_name}の形式が不正です")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise CredentialValidationError(f"b_route.{field_name}の形式が不正です") from None
    if len(encoded) != expected_bytes or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise CredentialValidationError(f"b_route.{field_name}の形式が不正です")
    return value


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
