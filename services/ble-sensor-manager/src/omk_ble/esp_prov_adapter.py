"""Narrow adapter over the fixed-version official Espressif provisioning client."""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from types import ModuleType


ESP_IDF_VERSION = "6.0.1"
NETWORK_PROVISIONING_VERSION = "1.2.4"
DEFAULT_TOOLING_ROOT = Path("/opt/omk/esp-provisioning/current")
PROVISIONING_SERVICE_UUID = "c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27"


class EspProvisioningToolingError(RuntimeError):
    pass


def tooling_root() -> Path:
    return Path(os.getenv("OMK_ESP_PROVISIONING_ROOT", str(DEFAULT_TOOLING_ROOT)))


def _paths(root: Path) -> tuple[Path, Path, Path]:
    component = root / f"network_provisioning-{NETWORK_PROVISIONING_VERSION}"
    esp_prov_path = component / "tool" / "esp_prov"
    protocomm_root = root / f"esp-idf-{ESP_IDF_VERSION}-protocomm"
    return component, esp_prov_path, protocomm_root


def _manifest_version(manifest: str) -> str | None:
    """Read the scalar ``version`` value used by an IDF component manifest."""
    for line in manifest.splitlines():
        match = re.match(r"^\s*version\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def ensure_available(root: Path | None = None) -> Path:
    root = root or tooling_root()
    component, esp_prov_path, protocomm_root = _paths(root)
    protocomm_path = protocomm_root / "components" / "protocomm" / "python"
    required = (
        component / "LICENSE",
        component / "idf_component.yml",
        esp_prov_path / "esp_prov.py",
        esp_prov_path / "transport" / "transport_ble.py",
        protocomm_root / "LICENSE",
        protocomm_path / "session_pb2.py",
        protocomm_root / "tools" / "cmake" / "version.cmake",
    )
    try:
        component_version = (component / "idf_component.yml").read_text(encoding="utf-8")
        idf_version = (protocomm_root / "tools" / "cmake" / "version.cmake").read_text(encoding="utf-8")
    except OSError as error:
        raise EspProvisioningToolingError("ESP provisioning tooling is unavailable") from error
    if (
        not all(path.is_file() for path in required)
        or _manifest_version(component_version) != NETWORK_PROVISIONING_VERSION
        or "set(IDF_VERSION_MAJOR 6)" not in idf_version
        or "set(IDF_VERSION_MINOR 0)" not in idf_version
        or "set(IDF_VERSION_PATCH 1)" not in idf_version
    ):
        raise EspProvisioningToolingError("ESP provisioning tooling is unavailable")
    return root


def _load_client(root: Path | None = None) -> ModuleType:
    root = ensure_available(root)
    _, esp_prov_path, protocomm_root = _paths(root)
    protocomm_path = protocomm_root / "components" / "protocomm" / "python"
    # The official ESP-IDF module intentionally uses absolute imports (prov,
    # security, transport and proto). Keep that implementation isolated here.
    for path in (str(protocomm_path), str(esp_prov_path)):
        if path not in sys.path:
            sys.path.insert(0, path)
    # network_provisioning's official proto loader resolves the generated
    # protocomm files through IDF_PATH. Point it only to the fixed minimal
    # ESP-IDF v6.0.1 protocomm tree under /opt.
    os.environ["IDF_PATH"] = str(protocomm_root)
    try:
        return importlib.import_module("esp_prov")
    except (ImportError, ModuleNotFoundError) as error:
        raise EspProvisioningToolingError("ESP provisioning tooling is unavailable") from error


async def provision_wifi(service_name: str, pop: str, ssid: str, passphrase: str) -> None:
    """Perform only Espressif's Security 1 / BLE SetConfig / ApplyConfig flow."""
    client = _load_client()
    try:
        # Use ESP-IDF's own BLE transport, including its endpoint discovery,
        # while retaining OMK's non-default provisioning service UUID as the
        # fallback for a device whose advertisement is temporarily cached.
        transport_module = importlib.import_module("transport")
        transport = transport_module.Transport_BLE(
            service_uuid=PROVISIONING_SERVICE_UUID,
            nu_lookup={"prov-session": "ff51", "prov-config": "ff52", "proto-ver": "ff53"},
        )
        await transport.connect(devname=service_name)
    except Exception as error:
        raise EspProvisioningToolingError("ESP provisioning device is unavailable") from error
    try:
        security = client.get_security(1, 0, "", "", pop, verbose=False)
        if security is None or not await client.establish_session(transport, security):
            raise EspProvisioningToolingError("ESP provisioning security session failed")
        if not await client.send_wifi_config(transport, security, ssid, passphrase):
            raise EspProvisioningToolingError("ESP provisioning Wi-Fi configuration failed")
        if not await client.apply_wifi_config(transport, security):
            raise EspProvisioningToolingError("ESP provisioning Wi-Fi configuration failed")
    except EspProvisioningToolingError:
        raise
    except Exception as error:
        # esp_prov errors can carry transport details. Do not expose those to
        # the API, and never include the values supplied to this function.
        raise EspProvisioningToolingError("ESP provisioning operation failed") from error
    finally:
        try:
            await transport.disconnect()
        except Exception:
            pass
