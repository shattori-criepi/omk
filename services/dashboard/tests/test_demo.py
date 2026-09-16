"""Demo must work on a fresh Gateway without developer data or services."""

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import httpx
import pytest

import app.main as dashboard_main
from app.data.display_repository import DisplayRepository
from app.demo import demo_candidates
from app.display_items import display_candidates
from app.recommendations import clock_item_ids, recommended_blocks

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
ENVIRONMENT = {"temperature", "humidity", "co2", "pm25", "voc", "nox"}
POWER = {"pv_power", "load_power", "grid_import", "grid_export", "battery_soc", "battery_power_bidirectional"}
OUTDOOR = {"outdoor_temperature", "outdoor_humidity"}
ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    async def no_service(*args, **kwargs):
        pytest.fail("Demo display must not need a sensor manager or optional service")

    monkeypatch.setattr(dashboard_main, "datetime", FixedDatetime)
    monkeypatch.setattr(dashboard_main, "_DERIVED_ENERGY_CACHE", None)
    monkeypatch.setattr(dashboard_main, "_ble_request", no_service)
    monkeypatch.setattr(dashboard_main, "_system_manager_request", no_service)
    def no_network(**kwargs):
        pytest.fail("Demo display must not query sensor-manager or other services")

    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", no_network)
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(tmp_path / "latest"))
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path / "processed"))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    with TestClient(dashboard_main.app) as client:
        yield client, tmp_path


def write_reading(root: Path, field: str, value: float, *, device: str = "environment", age: int = 0) -> str:
    topic = f"omk/{device}/{'power-flow' if device == 'pcs' else 'environment'}"
    item_id = "item_v1_" + hashlib.sha256(f"{topic}/{field}".encode()).hexdigest()
    received_at = (NOW - timedelta(seconds=age)).isoformat()
    record = dict(id=item_id, topic=topic, device_id=device, field=field, value_type="number", last_received_at=received_at)
    latest = root / "latest"
    (latest / "items").mkdir(parents=True, exist_ok=True)
    path = latest / "catalog.json"
    catalog = json.loads(path.read_text()) if path.exists() else {"version": 1, "items": []}
    catalog["items"].append(record)
    path.write_text(json.dumps(catalog))
    (latest / "items" / f"{item_id}.json").write_text(json.dumps({
        **record, "version": 1, "value": value, "received_at": received_at, "measured_at": received_at,
    }))
    return item_id


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def enable_demo(client: TestClient) -> None:
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": True}).status_code == 200


def snapshot(client: TestClient, mode: str) -> dict:
    response = client.get(f"/api/display?demo_mode={mode}")
    assert response.status_code == 200
    return response.json()


def readings(payload: dict) -> list[dict]:
    if payload["mode"] == "clock":
        return payload["supplemental"]
    return [item for block in payload["blocks"] for item in [block["primary"], *block["secondary"]]]


def assert_reading_badges(html: str, items: list[dict]) -> None:
    for item in items:
        markup = re.search(r'data-item-id="' + re.escape(item["id"]) + r'">(.*?)</div>', html, re.DOTALL)
        assert markup is not None
        badges = re.findall(r'data-role="source-kind" data-source-kind="(real|demo)">(実測|模擬)</span>', markup.group(1))
        assert badges == [(item["source_kind"], "模擬" if item["source_kind"] == "demo" else "実測")]
        assert "（模擬）" not in item["short_label"]


def test_fresh_gateway_demo_without_any_configuration_data_or_services(gateway):
    client, root = gateway
    assert files(root) == {}
    # Enabling demo also exercises first-time settings creation, with no catalog.
    enable_demo(client)
    settings_path = root / "dashboard" / "settings.json"
    settings = json.loads(settings_path.read_text())
    assert settings["presets"]["recommended"]["blocks"] == []
    assert settings["presets"]["clock"]["item_ids"] == []
    before = files(root)
    for _ in range(2):
        recommended = snapshot(client, "recommended")
        by_role = {item["semantic_role"]: item for item in readings(recommended)}
        assert ENVIRONMENT | {"grid_power"} <= by_role.keys()
        assert by_role["grid_power"]["value"] == "-0.01"
        assert by_role["grid_power"]["unit"] == "kW"
        assert all(by_role[role]["source_kind"] == "demo" for role in ENVIRONMENT | {"grid_power"})
        clock = snapshot(client, "clock")
        assert clock["time"] == "12:00"
        assert [item["semantic_role"] for item in readings(clock)] == ["grid_power", "temperature", "humidity", "co2"]
        for mode in ("custom", "recommended", "clock"):
            response = client.get(f"/display?demo_mode={mode}")
            assert response.status_code == 200
            assert f'data-dashboard-mode="{mode}"' in response.text
            assert 'data-source-kind="demo">模擬</span>' in response.text
            assert_reading_badges(response.text, readings(snapshot(client, mode)))
        custom = snapshot(client, "custom")
        assert [(block["group"], block["size"]) for block in custom["blocks"]] == [
            ("パワコン", "large"), ("室内環境", "medium"), ("外気", "small"),
        ]
        assert [len([block["primary"], *block["secondary"]]) for block in custom["blocks"]] == [6, 6, 2]
        assert {item["semantic_role"] for item in readings(custom)} == POWER | ENVIRONMENT | OUTDOOR
        assert all(item["source_kind"] == "demo" for item in readings(custom))
    assert files(root) == before
    assert not (root / "latest").exists()
    assert not (root / "processed").exists()
    assert not (root / "registered_sensors.json").exists()


def test_pcs_only_keeps_custom_real_and_fills_automatic_presets(gateway):
    client, root = gateway
    load_id = write_reading(root, "load_power_w", 2450, device="pcs")
    write_reading(root, "pv_power_w", 820, device="pcs")
    enable_demo(client)
    before = files(root)
    custom = readings(snapshot(client, "custom"))
    load = next(item for item in custom if item["id"] == load_id)
    assert load["value"] == "2.45"
    assert load["source_kind"] == "real"
    assert all(item["source_kind"] == "demo" for item in custom if item["semantic_role"] not in {"load_power", "pv_power"})
    assert "grid_power" not in {item["semantic_role"] for item in custom}
    for mode in ("recommended", "clock"):
        displayed = readings(snapshot(client, mode))
        assert {"grid_power", "temperature", "humidity", "co2"} <= {item["semantic_role"] for item in displayed}
        assert all(item["source_kind"] == "demo" for item in displayed if item["semantic_role"] in ENVIRONMENT | {"grid_power"})
    assert files(root) == before


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("age,expected_freshness", [(0, "normal"), (420, "delayed"), (660, "unavailable")])
def test_environment_full_or_partial_prioritizes_real_readings(gateway, partial, age, expected_freshness):
    client, root = gateway
    temperature_id = write_reading(root, "temperature_celsius", 21.3, age=age)
    if not partial:
        write_reading(root, "relative_humidity_percent", 57, age=age)
        write_reading(root, "co2_ppm", 730, age=age)
    enable_demo(client)
    before = files(root)
    for mode in ("custom", "recommended", "clock"):
        displayed = {item["semantic_role"]: item for item in readings(snapshot(client, mode))}
        temperature = displayed["temperature"]
        assert temperature["id"] == temperature_id
        assert temperature["value"] == ("27.9" if expected_freshness == "unavailable" else "21.3")
        assert temperature["source_kind"] == ("demo" if expected_freshness == "unavailable" else "real")
        if expected_freshness != "unavailable":
            assert temperature["freshness"] == expected_freshness
        if mode != "custom":
            assert displayed["grid_power"]["value"] == "-0.01"
            assert displayed["humidity"]["value"] == ("40" if partial or age == 660 else "57")
            assert displayed["co2"]["value"] == ("615" if partial or age == 660 else "730")
    assert files(root) == before


def test_catalog_without_latest_uses_fixture_and_leaves_recommended_when_off(gateway):
    client, root = gateway
    item_id = write_reading(root, "temperature_celsius", 21.3)
    (root / "latest" / "items" / f"{item_id}.json").unlink()
    enable_demo(client)
    for mode in ("custom", "recommended", "clock"):
        temperature = next(item for item in readings(snapshot(client, mode)) if item["id"] == item_id)
        assert temperature["value"] == "27.9"
        assert temperature["source_kind"] == "demo"
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    assert item_id not in {item["id"] for item in readings(snapshot(client, "recommended"))}
    # Missing readings remain available to the custom editor.
    items = client.get("/api/admin/display-items").json()
    temperature = next(item for group in items["groups"] for item in group["items"] if item["id"] == item_id)
    assert temperature["freshness"] == "unavailable"


@pytest.mark.parametrize("with_sensor", [False, True])
def test_demo_never_leaks_into_admin_saved_presets_or_normal_display(gateway, with_sensor):
    client, root = gateway
    if with_sensor:
        write_reading(root, "temperature_celsius", 21.3)
    normal = client.get("/api/display").json()
    original_settings = client.get("/api/admin/dashboard-settings").json()
    enable_demo(client)
    before = files(root)
    for mode in ("recommended", "clock", "custom"):
        snapshot(client, mode)
    assert files(root) == before
    assert "demo:" not in client.get("/api/admin/display-items").text
    assert "模擬" not in client.get("/api/admin/display-items").text
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    assert client.get("/api/admin/dashboard-settings").json() == original_settings
    for mode in ("recommended", "clock", "custom"):
        assert snapshot(client, mode) == normal
        assert "demo:" not in client.get(f"/display?demo_mode={mode}").text

    # Saving/refreshing real presets while demo is enabled must also stay clean.
    enable_demo(client)
    assert client.post("/api/admin/dashboard-settings/recommended").status_code == 200
    assert client.post("/api/admin/dashboard-settings/mode", json={"mode": "clock"}).status_code == 200
    settings = client.get("/api/admin/dashboard-settings").json()
    assert client.put("/api/admin/dashboard-settings", json=settings).status_code == 200
    assert all(b"demo:" not in content for content in files(root).values())
    assert len(DisplayRepository(root / "latest").catalog()) == int(with_sensor)


def test_overlay_covers_all_roles_without_mutating_inputs():
    overlay = demo_candidates([])
    assert {item.semantic_role for item in overlay} == ENVIRONMENT | POWER | {"grid_power"}
    assert all(item.id.startswith("demo:") and item.value != "--" and item.source_kind == "demo" for item in overlay)
    assert len({item.id for item in overlay}) == len(overlay)
    fixture = json.loads((Path(dashboard_main.APP_DIR) / "demo" / "readings.json").read_text())["values"]
    assert float(fixture["grid_power"]) == float(fixture["grid_import"]) - float(fixture["grid_export"])


@pytest.mark.parametrize("freshness", ["stale", "unavailable", "delayed", "normal"])
def test_overlay_freshness_priority_across_environment_sources(freshness):
    # A complete stale sensor must not hide a real temperature on another device.
    stale = [replace(item, id=f"old:{item.semantic_role}", group="a-old", device_id="old", freshness="unavailable", value="--", short_label="old", source_kind="real")
             for item in demo_candidates([]) if item.semantic_role in ENVIRONMENT]
    actual = replace(stale[0], id="actual-temperature", group="z-new", device_id="new", value="20.1", freshness=freshness, short_label="温度")
    inputs = [*stale, actual]
    before = list(inputs)
    overlay = demo_candidates(inputs)
    assert inputs == before
    chosen_ids = clock_item_ids(overlay)
    chosen = [item for item in overlay if item.id in chosen_ids and item.semantic_role == "temperature"]
    assert len(chosen) == 1
    if freshness in {"normal", "delayed"}:
        assert chosen[0].id == actual.id
        assert chosen[0].value == "20.1"
        assert chosen[0].source_kind == "real"
    else:
        assert chosen[0].value == "27.9"
        assert chosen[0].source_kind == "demo"
    recommended_ids = {item_id for block in recommended_blocks(overlay) for item_id in block.item_ids}
    assert chosen[0].id in recommended_ids


def test_normal_beats_delayed_and_split_environment_keeps_all_real_values(gateway):
    client, root = gateway
    write_reading(root, "temperature_celsius", 18.0, device="a-old", age=420)
    normal_id = write_reading(root, "temperature_celsius", 21.3, device="z-new")
    humidity_id = write_reading(root, "relative_humidity_percent", 57, device="a-old")
    co2_id = write_reading(root, "co2_ppm", 730, device="co2-only", age=420)
    enable_demo(client)
    displayed = {item["id"]: item for item in readings(snapshot(client, "clock"))}
    assert {normal_id, humidity_id, co2_id} <= displayed.keys()
    assert all(displayed[item_id]["source_kind"] == "real" for item_id in (normal_id, humidity_id, co2_id))
    assert displayed[co2_id]["freshness"] == "delayed"
    # Source grouping stays unchanged in the repository/admin candidate path.
    candidates = display_candidates(DisplayRepository(root / "latest"), NOW)
    assert next(item for item in candidates if item.id == normal_id).group == "z-new"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
@pytest.mark.parametrize("mode", ["custom", "recommended", "clock"])
def test_demo_polling_renders_new_real_membership_and_switches_off(gateway, mode):
    client, root = gateway
    enable_demo(client)
    html = client.get(f"/display?demo_mode={mode}").text
    initial = json.loads(re.search(r'<script id="demo-snapshot" type="application/json">(.*?)</script>', html).group(1))
    unchanged = snapshot(client, mode)
    real_id = write_reading(root, "temperature_celsius", 21.3)
    changed = snapshot(client, mode)
    assert real_id in {item["id"] for item in readings(changed)}
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    disabled = snapshot(client, mode)
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const [initial, unchanged, changed, disabled] = JSON.parse(process.argv[2]);
const navigations = [];
let reloads = 0;
global.document = {
  body: {dataset: {demoEnabled: "true", demoMode: initial.mode, dashboardMode: initial.mode}},
  documentElement: {classList: {add() {}}},
  querySelector(selector) { return selector === "#demo-snapshot" ? {textContent: JSON.stringify(initial)} : null; },
};
global.CSS = {escape(value) { return value; }};
global.window = {setInterval() {}, location: {assign(url) { navigations.push(url); }, reload() { reloads++; }}};
global.fetch = async () => ({ok: true, json: async () => unchanged});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
updateDisplay(unchanged);
assert.deepStrictEqual(navigations, []);
updateDisplay(changed);
assert.deepStrictEqual(navigations, [`/display?demo_mode=${initial.mode}`]);
updateDisplay(disabled);
assert.strictEqual(reloads, 1);
'''
    subprocess.run(["node", "-e", harness, str(dashboard_main.APP_DIR / "static" / "display.js"),
                    json.dumps([initial, unchanged, changed, disabled])], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("mode", ["custom", "recommended", "clock"])
@pytest.mark.parametrize("layout", ["hero", "strip", "compact"])
def test_mixed_real_and_demo_sources_have_per_value_badges_only_when_enabled(gateway, mode, layout):
    client, root = gateway
    load = write_reading(root, "load_power_w", 2450, device="pcs")
    temperature = write_reading(root, "temperature_celsius", 21.3)
    humidity = write_reading(root, "relative_humidity_percent", 57, age=660)
    co2 = write_reading(root, "co2_ppm", 730, age=660)
    grid = write_reading(root, "net_power_w", 1230, device="broute", age=660)
    # A registered item can also lack its latest record entirely.
    (root / "latest" / "items" / f"{humidity}.json").unlink()
    settings = client.get("/api/admin/dashboard-settings").json()
    settings["presets"]["standard"]["blocks"] = [
        dict(block_id=group, group=group, title=group, size=size, layout_pattern=layout,
             primary_item_id=ids[0], item_ids=ids)
        for group, size, ids in [("パワコン", "small", [load]),
                                 ("environment", "large", [temperature, humidity, co2]),
                                 ("電力メーター（Bルート）", "medium", [grid])]
    ]
    assert client.put("/api/admin/dashboard-settings", json=settings).status_code == 200
    assert client.post("/api/admin/dashboard-settings/mode", json={"mode": mode}).status_code == 200
    normal_settings = (root / "dashboard" / "settings.json").read_bytes()
    normal_html = client.get("/display").text
    assert 'data-role="source-kind"' not in normal_html
    enable_demo(client)
    before = files(root)
    displayed = readings(snapshot(client, mode))
    by_id = {item["id"]: item for item in displayed}
    assert by_id[temperature]["source_kind"] == "real"
    assert by_id[temperature]["value"] == "21.3"
    for item_id in ((humidity, co2) if mode == "custom" else (grid, humidity, co2)):
        assert by_id[item_id]["source_kind"] == "demo"
    if mode == "custom":
        assert grid not in by_id
        assert by_id[load]["source_kind"] == "real"
        assert by_id[load]["value"] == "2.45"
    html = client.get(f"/display?demo_mode={mode}").text
    assert_reading_badges(html, displayed)
    assert files(root) == before
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    assert (root / "dashboard" / "settings.json").read_bytes() == normal_settings
    assert client.get("/display").text == normal_html
    assert all(item["source_kind"] == "real" for item in readings(snapshot(client, mode)))


@pytest.mark.parametrize("age,kind,value", [(0, "real", "3.15"), (660, "demo", "0.01")])
def test_pcs_auxiliary_uses_the_displayed_value_source(gateway, age, kind, value):
    client, root = gateway
    write_reading(root, "load_power_w", 2450, device="pcs")
    write_reading(root, "grid_export_power_w", 3150, device="pcs", age=age)
    enable_demo(client)
    block = snapshot(client, "custom")["blocks"][0]
    assert block["primary"]["source_kind"] == "real"
    assert block["auxiliary_source_kind"] == kind
    assert block["auxiliary_value"] == value
    html = client.get("/display?demo_mode=custom").text
    auxiliary = re.search(r'data-role="auxiliary"(.*?)</p>', html, re.DOTALL).group(1)
    assert f'data-source-kind="{kind}">{"模擬" if kind == "demo" else "実測"}</span>' in auxiliary
    assert files(root)["latest/catalog.json"].find(b"source_kind") == -1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
@pytest.mark.parametrize("mode", ["custom", "recommended", "clock"])
def test_polling_updates_source_badges_without_changing_item_identity(gateway, mode):
    client, root = gateway
    temperature = write_reading(root, "temperature_celsius", 21.3)
    enable_demo(client)
    real = snapshot(client, mode)
    path = root / "latest" / "items" / f"{temperature}.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "received_at": (NOW - timedelta(seconds=660)).isoformat()}))
    demo = snapshot(client, mode)
    assert next(item for item in readings(real) if item["id"] == temperature)["source_kind"] == "real"
    assert next(item for item in readings(demo) if item["id"] == temperature)["source_kind"] == "demo"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const [real, demo, temperature] = JSON.parse(process.argv[2]);
const nodes = new Map();
const leaf = () => ({textContent: "", dataset: {}, hidden: false});
const classList = {add() {}, remove() {}};
const items = real.mode === "clock" ? real.supplemental : real.blocks.flatMap(b => [b.primary, ...b.secondary]);
for (const item of items) {
  const children = Object.fromEntries(["label-text", "source-kind", "value", "unit"].map(role => [role, leaf()]));
  nodes.set(`[data-item-id="${item.id}"]`, {
    children, classList,
    querySelector(selector) { return children[selector.match(/data-role="([^"]+)"/)[1]] || null; },
  });
}
for (const block of real.blocks || []) nodes.set(`[data-block-id="${block.id}"]`, {classList, querySelector() {return null;}});
global.document = {
  body: {dataset: {demoEnabled: "true", demoMode: real.mode, dashboardMode: real.mode}},
  documentElement: {classList},
  querySelector(selector) {return selector === "#demo-snapshot" ? {textContent: JSON.stringify(real)} : nodes.get(selector) || null;},
};
global.CSS = {escape(value) {return value;}};
global.window = {setInterval() {}, location: {assign() {throw Error("unexpected navigation");}, reload() {throw Error("unexpected reload");}}};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const node = nodes.get(`[data-item-id="${temperature}"]`);
for (const [data, source, badge, value] of [[real, "real", "実測", "21.3"], [demo, "demo", "模擬", "27.9"], [real, "real", "実測", "21.3"]]) {
  updateDisplay(data);
  assert.strictEqual(node.children["source-kind"].dataset.sourceKind, source);
  assert.strictEqual(node.children["source-kind"].textContent, badge);
  assert.strictEqual(node.children["label-text"].textContent, "温度");
  assert.strictEqual(node.children.value.textContent, value);
}
'''
    subprocess.run(["node", "-e", harness, str(dashboard_main.APP_DIR / "static" / "display.js"),
                    json.dumps([real, demo, temperature])], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("device", ["pcs", "sen66", "broute"])
@pytest.mark.parametrize("age", [0, 420])
def test_single_device_gateway_has_real_values_only_where_available(gateway, device, age):
    client, root = gateway
    fields = {
        "pcs": {"load_power_w": 2450, "pv_power_w": 820, "grid_import_power_w": 1200,
                "grid_export_power_w": 0, "battery_soc_percent": 72,
                "battery_charge_power_w": 0, "battery_discharge_power_w": 430},
        "sen66": {"temperature_celsius": 21.3, "relative_humidity_percent": 57,
                  "co2_ppm": 730, "pm2_5_ug_m3": 2.4, "voc_index": 83, "nox_index": 2},
        "broute": {"net_power_w": 1200},
    }[device]
    for field, value in fields.items():
        write_reading(root, field, value, device=device, age=age)
    enable_demo(client)
    before = files(root)
    real_roles = {"pcs": POWER, "sen66": ENVIRONMENT, "broute": {"grid_power"}}[device]
    for mode in ("custom", "recommended", "clock", "custom"):
        payload = snapshot(client, mode)
        displayed = readings(payload)
        relevant = [item for item in displayed if item["semantic_role"] in POWER | ENVIRONMENT | OUTDOOR | {"grid_power"}]
        for item in relevant:
            assert item["source_kind"] == ("real" if item["semantic_role"] in real_roles else "demo")
            if item["source_kind"] == "real":
                assert item["freshness"] == ("delayed" if age else "normal")
        if mode == "custom":
            assert len(payload["blocks"]) == 3
            assert {item["semantic_role"] for item in displayed} == POWER | ENVIRONMENT | OUTDOOR
        else:
            assert next(item for item in displayed if item["semantic_role"] == "grid_power")["source_kind"] == ("real" if device == "broute" else "demo")
        response = client.get(f"/display?demo_mode={mode}")
        assert response.status_code == 200
        assert_reading_badges(response.text, displayed)
    assert files(root) == before


@pytest.mark.parametrize("with_sensor", [False, True])
def test_fixed_demo_custom_is_transient_and_cannot_be_saved(gateway, with_sensor):
    client, root = gateway
    item_id = write_reading(root, "temperature_celsius", 21.3) if with_sensor else None
    settings = client.get("/api/admin/dashboard-settings").json()
    settings["mode"] = "custom"
    settings["presets"]["standard"]["blocks"] = ([dict(
        block_id="my-choice", group="environment", title="元のカスタム", size="small",
        layout_pattern="strip", primary_item_id=item_id, item_ids=[item_id],
    )] if with_sensor else [])
    settings["presets"]["recommended"]["blocks"] = []
    settings["presets"]["clock"]["item_ids"] = []
    assert client.put("/api/admin/dashboard-settings", json=settings).status_code == 200
    normal = client.get("/api/display").json()
    original_files = files(root)
    enable_demo(client)
    before_display = files(root)
    for _ in range(2):
        custom = snapshot(client, "custom")
        assert [(block["size"], block["title"]) for block in custom["blocks"]] == [
            ("large", "パワコン"), ("medium", "室内環境"), ("small", "外気"),
        ]
        snapshot(client, "recommended")
        snapshot(client, "clock")
    assert files(root) == before_display
    # The normal settings endpoint must also reject an attempt to save the overlay.
    attempted = client.get("/api/admin/dashboard-settings").json()
    attempted["presets"]["standard"]["blocks"] = [dict(
        block_id=block["id"], group=block["group"], title=block["title"], size=block["size"],
        layout_pattern=block["layout_pattern"], primary_item_id=block["primary"]["id"],
        item_ids=[item["id"] for item in [block["primary"], *block["secondary"]]],
    ) for block in custom["blocks"]]
    assert client.put("/api/admin/dashboard-settings", json=attempted).status_code == 400
    assert files(root) == before_display
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    assert client.get("/api/display").json() == normal
    assert files(root) == original_files


@pytest.mark.parametrize("manager_available", [False, True])
def test_outdoor_is_always_fixed_demo_even_with_registered_outdoor_readings(gateway, monkeypatch, manager_available):
    client, root = gateway
    write_reading(root, "temperature_c", 12.3, device="th-001")
    write_reading(root, "relative_humidity_percent", 85, device="th-001")
    registered = {"sensors": [dict(sensor_id="th-001", sensor_type="environment",
                                   model="waterproof_sensor", location="屋外", enabled=True)]}
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=registered) if manager_available else httpx.Response(503)

    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", lambda **kwargs: ASYNC_CLIENT(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    normal = client.get("/api/display").json()
    enable_demo(client)
    before = files(root)
    for _ in range(3):
        custom = snapshot(client, "custom")
        assert [(block["group"], block["size"]) for block in custom["blocks"]] == [
            ("パワコン", "large"), ("室内環境", "medium"), ("外気", "small"),
        ]
        outdoor = custom["blocks"][2]
        outdoor_items = [outdoor["primary"], *outdoor["secondary"]]
        assert [(item["id"], item["short_label"], item["value"], item["unit"], item["source_kind"]) for item in outdoor_items] == [
            ("demo:outdoor_temperature", "外気温", "25.1", "℃", "demo"),
            ("demo:outdoor_humidity", "外気相対湿度", "50", "%", "demo"),
        ]
        html = client.get("/display?demo_mode=custom").text
        assert_reading_badges(html, readings(custom))
        card = re.search(r'<article[^>]*data-block-id="demo:custom:outdoor".*?</article>', html, re.DOTALL).group(0)
        assert "display-card--stacked" in card
        assert 'class="display-card-stacked-items"' in card
        assert card.index('data-item-id="demo:outdoor_temperature"') < card.index('data-item-id="demo:outdoor_humidity"')
        assert card.count('data-role="label"') == card.count('data-role="value"') == card.count('data-role="unit"') == 2
    assert calls == []  # Registry availability/contents cannot influence the display.
    assert files(root) == before
    assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": False}).status_code == 200
    assert client.get("/api/display").json() == normal
    assert 'data-source-kind="demo"' not in client.get("/display").text
