"""Durable OMK site identity synchronized with SORACOM subscriber tags."""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Final

import httpx

LOGGER = logging.getLogger(__name__)
METADATA_SERVICE_URL: Final = "http://metadata.soracom.io/v1/subscriber"
SITE_UUID_TAG: Final = "site_uuid"
DEFAULT_TIMEOUT_SECONDS: Final = 5.0


class SiteUUIDError(RuntimeError):
    """The site identity could not be safely determined."""


class InvalidSiteUUIDError(SiteUUIDError):
    """A stored site UUID is not a UUID v4."""


class MetadataServiceError(SiteUUIDError):
    """The SORACOM Metadata Service request failed."""


def validate_site_uuid(value: object, *, source: str) -> str:
    """Return the canonical UUID v4 string, or raise without replacing it."""

    if not isinstance(value, str):
        raise InvalidSiteUUIDError(f"{source}のsite_uuid形式が不正です")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise InvalidSiteUUIDError(f"{source}のsite_uuid形式が不正です") from None
    if parsed.version != 4:
        raise InvalidSiteUUIDError(f"{source}のsite_uuid形式が不正です")
    return str(parsed)


def read_local_site_uuid(path: Path) -> str | None:
    """Read and validate the durable local value; absence is not an error."""

    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SiteUUIDError("ローカルsite_uuidを読み込めませんでした") from error
    return validate_site_uuid(value, source="ローカル")


def write_local_site_uuid_atomically(path: Path, value: str) -> None:
    """Atomically persist a validated UUID, including the directory metadata."""

    value = validate_site_uuid(value, source="ローカル")
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".site_uuid.", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = None
            output.write(f"{value}\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync_directory(path.parent)
    except OSError as error:
        raise SiteUUIDError("ローカルsite_uuidを保存できませんでした") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


class SoracomMetadataClient:
    """Small synchronous client for the subscriber-tag Metadata Service API."""

    def __init__(self, *, base_url: str = METADATA_SERVICE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def get_site_uuid(self) -> str | None:
        try:
            response = httpx.get(f"{self._base_url}.tags", timeout=self._timeout_seconds)
            response.raise_for_status()
            return _site_uuid_from_response(response.json())
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise MetadataServiceError("SORACOM Metadata Serviceからsite_uuidを取得できませんでした") from error

    def put_site_uuid(self, value: str) -> None:
        value = validate_site_uuid(value, source="SORACOM")
        try:
            response = httpx.put(
                f"{self._base_url}/tags",
                json=[{"tagName": SITE_UUID_TAG, "tagValue": value}],
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MetadataServiceError("SORACOM Metadata Serviceへsite_uuidを保存できませんでした") from error


def resolve_site_uuid(path: Path, metadata: SoracomMetadataClient) -> str:
    """Reconcile local and SORACOM values without guessing on a conflict."""

    try:
        return _resolve_site_uuid(path, metadata)
    except InvalidSiteUUIDError:
        LOGGER.error("site_uuidのUUID形式が不正です")
        raise


def _resolve_site_uuid(path: Path, metadata: SoracomMetadataClient) -> str:
    """Implementation separated so invalid values receive one clear log record."""

    local_uuid = read_local_site_uuid(path)
    if local_uuid is None:
        local_uuid = str(uuid.uuid4())
        write_local_site_uuid_atomically(path, local_uuid)
        LOGGER.info("新しいsite_uuidをローカルに生成しました: %s", local_uuid)

    # The durable Gateway value is authoritative. SORACOM is an optional
    # mirror, so an unavailable modem or Metadata Service cannot stop OMK.
    try:
        soracom_value = metadata.get_site_uuid()
    except MetadataServiceError:
        LOGGER.warning("Metadata Serviceへ接続できません。ローカルsite_uuidを継続利用します")
        return local_uuid

    soracom_uuid = validate_site_uuid(soracom_value, source="SORACOM") if soracom_value is not None else None

    if soracom_uuid == local_uuid:
        LOGGER.info("ローカルとSORACOMのsite_uuidが一致しました")
        return local_uuid

    if soracom_uuid is not None:
        LOGGER.warning("ローカルとSORACOMのsite_uuidが不一致です。ローカル値をSORACOMへ反映します")
    _save_to_soracom(metadata, local_uuid)
    return local_uuid


def _save_to_soracom(metadata: SoracomMetadataClient, value: str) -> None:
    try:
        metadata.put_site_uuid(value)
    except MetadataServiceError:
        LOGGER.warning("SORACOM Tagへsite_uuidを保存できません。ローカルsite_uuidを継続利用します")
        return
    LOGGER.info("SORACOM Tagへsite_uuidを保存しました: %s", value)


def _site_uuid_from_response(payload: Any) -> str | None:
    """Extract the tag from the documented response and tolerate common shapes."""

    tags = payload.get("tags", payload) if isinstance(payload, dict) else payload
    if isinstance(tags, dict):
        value = tags.get(SITE_UUID_TAG)
        return value if value is None or isinstance(value, str) else _invalid_metadata_response()
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("tagName") == SITE_UUID_TAG:
                value = tag.get("tagValue")
                return value if isinstance(value, str) else _invalid_metadata_response()
        return None
    return _invalid_metadata_response()


def _invalid_metadata_response() -> None:
    raise ValueError("unexpected Metadata Service response")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
