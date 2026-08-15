import asyncio
from pathlib import Path

import pytest

from omk_ble.esp_prov_adapter import DEFAULT_TOOLING_ROOT, EspProvisioningToolingError, ensure_available
from omk_ble.ap_credentials import ApCredentialError, read_ap_psk
from omk_ble.node_credentials import NodeCredentialError, NodeCredentialStore
from omk_ble.node_provisioning import NodeProvisioner


NODE_ID = "112233445566"


def test_adapter_import_uses_only_stable_opt_runtime_path() -> None:
    assert DEFAULT_TOOLING_ROOT == Path("/opt/omk/esp-provisioning/current")


def _credential(directory: Path) -> NodeCredentialStore:
    directory.mkdir()
    path = directory / f"{NODE_ID}.json"
    path.write_text('{"node_id":"112233445566","provisioning_secret":"' + "a" * 64 + '"}', encoding="utf-8")
    path.chmod(0o600)
    return NodeCredentialStore(directory)


def test_node_credentials_require_expected_identity_secret_and_mode(tmp_path: Path) -> None:
    store = _credential(tmp_path / "nodes")
    assert len(store.read_pop(NODE_ID)) == 64
    (tmp_path / "nodes" / f"{NODE_ID}.json").chmod(0o640)
    with pytest.raises(NodeCredentialError):
        store.read_pop(NODE_ID)


def test_systemd_ap_credential_fails_closed_when_missing_or_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(ApCredentialError):
        read_ap_psk()
    (tmp_path / "omk_ap_psk").write_text("\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    with pytest.raises(ApCredentialError):
        read_ap_psk()


def test_tooling_check_accepts_only_complete_fixed_tree(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    component = root / "network_provisioning-1.2.4"
    protocomm = root / "esp-idf-6.0.1-protocomm"
    (component / "tool/esp_prov/transport").mkdir(parents=True)
    (protocomm / "components/protocomm/python").mkdir(parents=True)
    (protocomm / "tools/cmake").mkdir(parents=True)
    manifest = component / "idf_component.yml"
    manifest.write_text("version: 1.2.4\n", encoding="utf-8")
    (protocomm / "tools/cmake/version.cmake").write_text("set(IDF_VERSION_MAJOR 6)\nset(IDF_VERSION_MINOR 0)\nset(IDF_VERSION_PATCH 1)\n", encoding="utf-8")
    for path in (component / "LICENSE", component / "tool/esp_prov/esp_prov.py", component / "tool/esp_prov/transport/transport_ble.py", protocomm / "LICENSE", protocomm / "components/protocomm/python/session_pb2.py"):
        path.write_text("", encoding="utf-8")
    assert ensure_available(root) == root
    for version_line in ('  version: "1.2.4" # official component\n', "version: '1.2.4'\n"):
        manifest.write_text(version_line, encoding="utf-8")
        assert ensure_available(root) == root
    manifest.write_text('version: "1.2.5"\n', encoding="utf-8")
    with pytest.raises(EspProvisioningToolingError):
        ensure_available(root)
    manifest.write_text("version: 1.2.4\n", encoding="utf-8")
    (component / "tool/esp_prov/esp_prov.py").unlink()
    with pytest.raises(EspProvisioningToolingError):
        ensure_available(root)


def test_provisioning_job_uses_control_then_official_flow_and_waits_for_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        import omk_ble.node_provisioning as module
        monkeypatch.setattr(module, "ensure_available", lambda: Path("/official/tooling"))
        events: list[str] = []
        state = {NODE_ID: "unregistered"}

        async def pause() -> None:
            events.append("pause")

        async def resume() -> None:
            events.append("resume")

        async def control(node_id: str) -> None:
            assert node_id == NODE_ID
            events.append("control")

        async def provision(service_name: str, pop: str, ssid: str, psk: str) -> None:
            assert service_name == f"OMK_{NODE_ID}"
            assert len(pop) == 64
            assert ssid == "omk-ap"
            assert psk == "not-logged"
            events.append("provision")
            state[NODE_ID] = "provisioned"

        provisioner = NodeProvisioner(
            pause, resume, state.get, _credential(tmp_path / "nodes"), control, provision,
            ssid_reader=lambda: "omk-ap", psk_reader=lambda: "not-logged", timeout_seconds=1,
        )
        job = await provisioner.start(NODE_ID)
        await provisioner._active
        assert job.state == "provisioned"
        assert job.error is None
        assert events == ["pause", "control", "provision", "resume"]

    asyncio.run(scenario())


def test_provisioning_failure_resumes_passive_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        import omk_ble.node_provisioning as module
        monkeypatch.setattr(module, "ensure_available", lambda: Path("/official/tooling"))
        events: list[str] = []

        async def mark(value: str) -> None:
            events.append(value)

        provisioner = NodeProvisioner(
            lambda: mark("pause"), lambda: mark("resume"), lambda _: "unregistered",
            _credential(tmp_path / "nodes"), lambda _: mark("control"),
            psk_reader=lambda: (_ for _ in ()).throw(RuntimeError("missing")),
        )
        job = await provisioner.start(NODE_ID)
        await provisioner._active
        assert job.state == "failed"
        assert job.error == "Wi-Fi provisioning failed"
        assert events == []

    asyncio.run(scenario())
