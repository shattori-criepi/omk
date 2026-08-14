from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
import httpx

from omk_system_manager.site_uuid import (
    InvalidSiteUUIDError,
    MetadataServiceError,
    SiteUUIDError,
    SoracomMetadataClient,
    read_local_site_uuid,
    resolve_site_uuid,
    write_local_site_uuid_atomically,
)


def new_uuid() -> str:
    return str(uuid.uuid4())


class MetadataStub:
    def __init__(self, value: str | None, *, get_error: Exception | None = None, put_error: Exception | None = None) -> None:
        self.value = value
        self.get_error = get_error
        self.put_error = put_error
        self.saved: list[str] = []

    def get_site_uuid(self) -> str | None:
        if self.get_error:
            raise self.get_error
        return self.value

    def put_site_uuid(self, value: str) -> None:
        if self.put_error:
            raise self.put_error
        self.saved.append(value)


def test_matching_local_and_soracom_values_are_used_without_writes(tmp_path: Path, caplog) -> None:
    value = new_uuid()
    path = tmp_path / "site_uuid"
    write_local_site_uuid_atomically(path, value)
    metadata = MetadataStub(value)

    with caplog.at_level(logging.INFO):
        assert resolve_site_uuid(path, metadata) == value

    assert metadata.saved == []
    assert "一致しました" in caplog.text


def test_soracom_value_is_restored_to_missing_local_storage(tmp_path: Path, caplog) -> None:
    value = new_uuid()
    path = tmp_path / "site_uuid"
    with caplog.at_level(logging.INFO):
        assert resolve_site_uuid(path, MetadataStub(value)) == value
    assert read_local_site_uuid(path) == value
    assert "復元しました" in caplog.text


def test_local_value_is_saved_when_soracom_tag_is_missing(tmp_path: Path) -> None:
    value = new_uuid()
    path = tmp_path / "site_uuid"
    write_local_site_uuid_atomically(path, value)
    metadata = MetadataStub(None)
    assert resolve_site_uuid(path, metadata) == value
    assert metadata.saved == [value]


def test_missing_values_generate_v4_and_save_both(tmp_path: Path, caplog) -> None:
    path = tmp_path / "site_uuid"
    metadata = MetadataStub(None)
    with caplog.at_level(logging.INFO):
        result = resolve_site_uuid(path, metadata)
    assert uuid.UUID(result).version == 4
    assert read_local_site_uuid(path) == result
    assert metadata.saved == [result]
    assert "生成しました" in caplog.text


def test_mismatched_values_fail_without_overwriting_either(tmp_path: Path, caplog) -> None:
    path = tmp_path / "site_uuid"
    local = new_uuid()
    remote = new_uuid()
    write_local_site_uuid_atomically(path, local)
    metadata = MetadataStub(remote)
    with caplog.at_level(logging.ERROR), pytest.raises(SiteUUIDError, match="不一致"):
        resolve_site_uuid(path, metadata)
    assert read_local_site_uuid(path) == local
    assert metadata.saved == []
    assert local in caplog.text and remote in caplog.text


@pytest.mark.parametrize("local, remote", [(None, "not-a-uuid"), ("not-a-uuid", None)])
def test_invalid_values_fail_without_replacement(tmp_path: Path, local: str | None, remote: str | None, caplog) -> None:
    path = tmp_path / "site_uuid"
    if local is not None:
        path.write_text(local, encoding="utf-8")
    with caplog.at_level(logging.ERROR), pytest.raises(InvalidSiteUUIDError):
        resolve_site_uuid(path, MetadataStub(remote))
    assert "UUID形式が不正" in caplog.text


def test_metadata_timeout_uses_existing_local_value(tmp_path: Path, caplog) -> None:
    value = new_uuid()
    path = tmp_path / "site_uuid"
    write_local_site_uuid_atomically(path, value)
    with caplog.at_level(logging.WARNING):
        assert resolve_site_uuid(path, MetadataStub(None, get_error=MetadataServiceError("timeout"))) == value
    assert "接続できません" in caplog.text


def test_initial_setup_fails_when_metadata_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(MetadataServiceError):
        resolve_site_uuid(tmp_path / "site_uuid", MetadataStub(None, get_error=MetadataServiceError("timeout")))


def test_soracom_write_failure_is_reported_and_local_value_remains(tmp_path: Path) -> None:
    value = new_uuid()
    path = tmp_path / "site_uuid"
    write_local_site_uuid_atomically(path, value)
    with pytest.raises(MetadataServiceError):
        resolve_site_uuid(path, MetadataStub(None, put_error=MetadataServiceError("write failed")))
    assert read_local_site_uuid(path) == value


def test_soracom_http_client_uses_documented_endpoints_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    value = new_uuid()
    calls: list[tuple[str, str, object, float]] = []

    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        calls.append(("GET", url, None, timeout))
        return httpx.Response(200, json={"tags": {"site_uuid": value}}, request=httpx.Request("GET", url))

    def fake_put(url: str, *, json: object, timeout: float) -> httpx.Response:
        calls.append(("PUT", url, json, timeout))
        return httpx.Response(200, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "put", fake_put)
    client = SoracomMetadataClient(timeout_seconds=2.5)

    assert client.get_site_uuid() == value
    client.put_site_uuid(value)
    assert calls == [
        ("GET", "http://metadata.soracom.io/v1/subscriber/tags", None, 2.5),
        ("PUT", "http://metadata.soracom.io/v1/subscriber/tags", [{"tagName": "site_uuid", "tagValue": value}], 2.5),
    ]
