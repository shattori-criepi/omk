from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from omk_system_manager.credentials import (
    CREDENTIALS_MODE,
    CredentialValidationError,
    CredentialWriteError,
    credential_status,
    validate_broute_credentials,
    write_credentials_atomically,
)

VALID_ID = "A" * 32
VALID_PASSWORD = "B" * 12


def test_valid_credentials_are_accepted() -> None:
    assert validate_broute_credentials(VALID_ID, VALID_PASSWORD) == (VALID_ID, VALID_PASSWORD)


@pytest.mark.parametrize(
    ("identifier", "password"),
    [
        ("A" * 31, VALID_PASSWORD),
        (VALID_ID, "B" * 11),
        ("Ａ" * 32, VALID_PASSWORD),
        ("A" * 31 + " ", VALID_PASSWORD),
        (VALID_ID, "B" * 11 + " "),
    ],
)
def test_invalid_credentials_do_not_echo_values(identifier: str, password: str) -> None:
    with pytest.raises(CredentialValidationError) as caught:
        validate_broute_credentials(identifier, password)
    assert identifier not in str(caught.value)
    assert password not in str(caught.value)


def test_atomic_write_generates_expected_yaml_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "credentials.yaml"

    write_credentials_atomically(path, VALID_ID, VALID_PASSWORD)

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "b_route": {"id": VALID_ID, "password": VALID_PASSWORD}
    }
    assert path.stat().st_mode & 0o777 == CREDENTIALS_MODE
    assert credential_status(path) == (True, "AAAA************************AAAA", True)


def test_replace_failure_preserves_old_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "credentials.yaml"
    old_content = "b_route:\n  id: OLD\n  password: OLD\n"
    path.write_text(old_content, encoding="utf-8")

    def fail_replace(_: str, __: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(CredentialWriteError):
        write_credentials_atomically(path, VALID_ID, VALID_PASSWORD)

    assert path.read_text(encoding="utf-8") == old_content
