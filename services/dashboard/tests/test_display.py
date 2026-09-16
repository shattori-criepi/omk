import asyncio
import json
import re
import shutil
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import duckdb
import pytest
from fastapi.testclient import TestClient

import app.main as dashboard_main
from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import LatestPower, ParquetRepository
from app.data.display_repository import DisplayRepository
from app.data.settings_repository import DashboardSettings, DisplayBlock, DisplaySelection, SettingsError, SettingsRepository
from app.demo import apply_demo_fallback
from app.display_items import DisplayBlockView, DisplayItem, candidate_for, catalog_items_with_latest, display_candidates, display_item_migrations, selected_blocks, selected_items
from app.metric_definitions import definition_for, format_value
from app.recommendations import clock_item_ids, recommended_blocks
from app.main import app
from app.view_models import (
    FreshnessStatus,
    PowerDirection,
    format_power,
    format_timestamp_seconds,
    freshness_for,
    get_display_view_model,
)

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 7, 30, 12, 0, 30, tzinfo=JST)
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_dashboard_settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep requests that create default display settings out of runtime data."""
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))


def _write_parquet(root: Path, dataset: str, columns: str, rows: list[tuple]) -> None:
    destination = root / dataset / "date=2026-07-30" / "data.parquet"
    destination.parent.mkdir(parents=True)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"CREATE TABLE records ({columns})")
        placeholders = ", ".join("?" for _ in rows[0])
        connection.executemany(f"INSERT INTO records VALUES ({placeholders})", rows)
        connection.execute(f"COPY records TO '{destination}' (FORMAT PARQUET)")
    finally:
        connection.close()


def _write_energy_data(root: Path) -> None:
    _write_parquet(
        root,
        "broute_interval_energy",
        "device_id VARCHAR, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, import_energy_kwh DOUBLE, export_energy_kwh DOUBLE, quality_status VARCHAR",
        [
            ("broute-001", datetime(2026, 7, 30, 11, 0, tzinfo=JST), datetime(2026, 7, 30, 11, 30, tzinfo=JST), 0.2, 0.4, "normal"),
            ("broute-001", datetime(2026, 7, 30, 11, 30, tzinfo=JST), datetime(2026, 7, 30, 12, 0, tzinfo=JST), 0.5, 0.9, "estimated"),
            ("broute-001", datetime(2026, 7, 29, 23, 0, tzinfo=JST), datetime(2026, 7, 29, 23, 30, tzinfo=JST), 9.0, 9.0, "normal"),
        ],
    )


def _write_latest(root: Path, filename: str, payload: dict, received_at: datetime = NOW) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "received_at": received_at.isoformat(timespec="milliseconds"),
        "topic": "omk/test-001/example",
        "qos": 0,
        "retain": False,
        "payload": payload,
    }
    (root / filename).write_text(json.dumps(record), encoding="utf-8")


def _write_generic_item(root: Path, suffix: str, *, topic: str, device_id: str, field: str, value: object, value_type: str = "number", received_at: datetime = NOW) -> str:
    item_id = f"item_v1_{suffix * 64}"
    item = {"version": 1, "id": item_id, "topic": topic, "device_id": device_id, "field": field, "value_type": value_type, "value": value, "measured_at": None, "received_at": received_at.isoformat(), "source": {"qos": 0, "retain": False}}
    (root / "items").mkdir(parents=True, exist_ok=True)
    (root / "items" / f"{item_id}.json").write_text(json.dumps(item), encoding="utf-8")
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"version": 1, "items": []}
    catalog["items"].append({key: item[key] for key in ("id", "topic", "device_id", "field", "value_type")} | {"last_received_at": item["received_at"]})
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    return item_id


def _block_payload(*blocks: dict) -> dict:
    return {"version": 2, "default_preset": "standard", "presets": {"standard": {"blocks": list(blocks)}}}


def _recommended_item(item_id: str, group: str, role: str | None) -> object:
    return SimpleNamespace(
        id=item_id, group=group, semantic_role=role, selectable=True,
        short_label=item_id, label=item_id,
    )


def test_recommended_blocks_rank_semantic_groups_and_exclude_ichijo() -> None:
    candidates = [
        _recommended_item("grid", "電力メーター（Bルート）", "grid_power"),
        _recommended_item("import", "電力メーター（Bルート）", "grid_import_energy"),
        _recommended_item("import-total", "電力メーター（Bルート）", "grid_import_energy_cumulative"),
        _recommended_item("export", "電力メーター（Bルート）", "grid_export_energy"),
        _recommended_item("derived:energy:today_import_kwh", "パワコン", "today_import_energy"),
        _recommended_item("temp", "multi", "temperature"),
        _recommended_item("humidity", "multi", "humidity"),
        _recommended_item("co2", "multi", "co2"),
        _recommended_item("pm25", "multi", "pm25"),
        _recommended_item("temp2", "simple", "temperature"),
        _recommended_item("humidity2", "simple", "humidity"),
        _recommended_item("plug", "plug-001", "device_power"),
        _recommended_item("ichijo", "パワコン", "load_power"),
    ]

    blocks = recommended_blocks(candidates)  # type: ignore[arg-type]

    assert [block.group for block in blocks] == ["電力メーター（Bルート）", "multi", "plug-001"]
    assert [block.size for block in blocks] == ["large", "medium", "small"]
    assert blocks[0].primary_item_id == "grid"
    assert blocks[0].item_ids == ("grid", "derived:energy:today_import_kwh")
    assert "import" not in blocks[0].item_ids and "export" not in blocks[0].item_ids
    assert blocks[1].item_ids[:3] == ("temp", "humidity", "co2")
    assert all(block.group != "パワコン" for block in blocks)


def test_co2_metric_uses_shared_user_facing_label_without_changing_identity() -> None:
    definition = definition_for("co2_ppm")

    assert definition.label == "CO₂濃度"
    assert definition.semantic_role == "co2"
    assert definition.unit == "ppm"


def test_clock_items_reuse_environment_ranking_and_only_include_grid_power() -> None:
    candidates = [
        _recommended_item("basic-temperature", "basic", "temperature"),
        _recommended_item("basic-humidity", "basic", "humidity"),
        _recommended_item("rich-temperature", "room-a", "temperature"),
        _recommended_item("rich-humidity", "room-a", "humidity"),
        _recommended_item("rich-co2", "room-a", "co2"),
        _recommended_item("rich-pm25", "room-a", "pm25"),
        _recommended_item("grid-power", "電力メーター（Bルート）", "grid_power"),
        _recommended_item("grid-import", "電力メーター（Bルート）", "grid_import_energy"),
        _recommended_item("ichijo", "パワコン", "load_power"),
        _recommended_item("plug", "plug-001", "device_power"),
    ]

    assert clock_item_ids(candidates) == (
        "grid-power", "rich-temperature", "rich-humidity", "rich-co2",
    )  # type: ignore[arg-type]
    assert clock_item_ids(candidates[:6]) == (
        "rich-temperature", "rich-humidity", "rich-co2",
    )  # type: ignore[arg-type]


def test_settings_v2_migrates_to_custom_and_new_settings_default_recommended(tmp_path: Path) -> None:
    path = tmp_path / "dashboard" / "settings.json"
    repository = SettingsRepository(path)
    groups = {"grid": "電力メーター（Bルート）"}
    recommended = [DisplayBlock("recommended_1", "電力メーター（Bルート）", "電力メーター（Bルート）", "large", "grid", ("grid",), "hero")]
    created = repository.load_or_create(groups, [], recommended_defaults=recommended)
    assert created.mode == "recommended"
    assert created.recommended_blocks == tuple(recommended)

    path.write_text(json.dumps(_block_payload({
        "block_id": "custom", "group": "電力メーター（Bルート）", "title": "以前の設定", "size": "large",
        "layout_pattern": "hero", "primary_item_id": "grid", "item_ids": ["grid"],
    })), encoding="utf-8")
    migrated = repository.load_or_create(groups, [])
    assert migrated.mode == "custom"
    assert migrated.custom_blocks[0].title == "以前の設定"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 3 and saved["mode"] == "custom"


def test_daily_broute_derived_item_can_belong_to_custom_and_recommended_blocks(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    derived = "derived:energy:today_import_kwh"
    settings = repository.save_payload({
        "version": 3, "mode": "custom", "default_preset": "standard",
        "presets": {
            "standard": {"blocks": [{
                "block_id": "ichijo", "group": "パワコン", "title": "パワコン", "size": "small",
                "layout_pattern": "compact", "primary_item_id": derived, "item_ids": [derived],
            }]},
            "recommended": {"blocks": [{
                "block_id": "recommended_1", "group": "電力メーター（Bルート）", "title": "電力メーター（Bルート）", "size": "large",
                "layout_pattern": "hero", "primary_item_id": "grid", "item_ids": ["grid", derived],
            }]},
        },
    }, {"grid": "電力メーター（Bルート）", derived: frozenset({"パワコン", "電力メーター（Bルート）"})})

    assert settings.custom_blocks[0].item_ids == (derived,)
    assert settings.recommended_blocks[0].item_ids == ("grid", derived)


def test_saved_ichijo_display_group_migrates_to_generic_power_conditioner(tmp_path: Path) -> None:
    path = tmp_path / "dashboard" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_block_payload({
        "block_id": "ichijo", "group": "一条パワコン", "title": "一条パワコン", "size": "small",
        "layout_pattern": "compact", "primary_item_id": "load", "item_ids": ["load"],
    })), encoding="utf-8")

    settings = SettingsRepository(path).load_or_create({"load": "パワコン"}, [])

    assert settings.custom_blocks[0].group == "パワコン"
    assert settings.custom_blocks[0].title == "パワコン"
    assert "一条パワコン" not in path.read_text(encoding="utf-8")


def test_clock_settings_keep_custom_and_recommended_presets(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    custom = DisplayBlock("custom", "room", "room", "small", "temperature", ("temperature",), "compact")
    recommended = DisplayBlock("recommended", "電力メーター（Bルート）", "電力メーター（Bルート）", "large", "grid", ("grid",), "hero")
    saved = repository.save_payload({
        "version": 3, "mode": "clock", "default_preset": "standard",
        "presets": {
            "standard": {"blocks": [custom.as_dict()]},
            "recommended": {"blocks": [recommended.as_dict()]},
            "clock": {"item_ids": ["temperature", "grid"]},
        },
    }, {"temperature": "room", "grid": "電力メーター（Bルート）"})

    assert saved.mode == "clock"
    assert saved.clock_item_ids == ("temperature", "grid")
    assert saved.custom_blocks == (custom,)
    assert saved.recommended_blocks == (recommended,)


def test_clock_supplemental_reorders_existing_slots_without_reselecting_them() -> None:
    candidates = [
        _clock_item("temperature", "living", "temperature"),
        _clock_item("humidity", "living", "humidity"),
        _clock_item("co2", "living", "co2"),
        _clock_item("grid", "電力メーター（Bルート）", "grid_power"),
    ]

    supplemental = dashboard_main._clock_supplemental(("temperature", "humidity", "co2", "grid"), candidates)

    assert [item.id for item in supplemental] == ["grid", "temperature", "humidity", "co2"]


def _clock_item(item_id: str, group: str, role: str | None, *, value: str = "1.0", unit: str = "") -> DisplayItem:
    label = "CO₂濃度" if role == "co2" else item_id
    return DisplayItem(item_id, label, group, "omk/test", group, item_id, "number", unit, "環境", role, True, NOW.isoformat(), value, "normal", short_label=label)


def test_clock_mode_switch_preserves_custom_and_recommended_and_renders_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        _clock_item("temperature", "living", "temperature", value="27.1", unit="°C"),
        _clock_item("humidity", "living", "humidity", value="45", unit="%"),
        _clock_item("co2", "living", "co2", value="620", unit="ppm"),
        _clock_item("grid", "電力メーター（Bルート）", "grid_power", value="1.8", unit="kW"),
        _clock_item("import", "電力メーター（Bルート）", "grid_import_energy", value="0.0", unit="kWh"),
        _clock_item("ichijo", "パワコン", "load_power", unit="kW"),
    ]
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    monkeypatch.setattr(dashboard_main, "_dashboard_candidates", lambda now=None: candidates)

    recommended = client.post("/api/admin/dashboard-settings/mode", json={"mode": "recommended"})
    assert recommended.status_code == 200
    original_recommended = recommended.json()["presets"]["recommended"]["blocks"]
    custom = client.post("/api/admin/dashboard-settings/mode", json={"mode": "custom"})
    assert custom.status_code == 200
    original_custom = custom.json()["presets"]["standard"]["blocks"]

    clock = client.post("/api/admin/dashboard-settings/mode", json={"mode": "clock"})
    assert clock.status_code == 200
    assert clock.json()["presets"]["clock"]["item_ids"] == ["grid", "temperature", "humidity", "co2"]
    assert clock.json()["presets"]["standard"]["blocks"] == original_custom
    assert clock.json()["presets"]["recommended"]["blocks"] == original_recommended

    snapshot = client.get("/api/display")
    assert snapshot.status_code == 200
    assert snapshot.json()["mode"] == "clock"
    assert snapshot.json()["date"].endswith("曜日")
    assert re.fullmatch(r"\d{2}:\d{2}", snapshot.json()["time"])
    assert snapshot.json()["freshness"] == "normal"
    assert snapshot.json()["updated_at"] != "--"
    assert [item["id"] for item in snapshot.json()["supplemental"]] == ["grid", "temperature", "humidity", "co2"]
    assert snapshot.json()["supplemental"][-1]["short_label"] == "CO₂濃度"
    assert "import" not in {item["id"] for item in snapshot.json()["supplemental"]}
    clock_html = client.get("/display").text
    for element_id in ("clock-date", "clock-time", "clock-updated-at", "clock-freshness", "clock-supplemental"):
        assert f'id="{element_id}"' in clock_html
    assert 'class="dashboard clock-dashboard"' in clock_html
    assert 'href="/admin"' in clock_html
    assert 'href="/admin/display"' not in clock_html

    assert client.post("/api/admin/dashboard-settings/mode", json={"mode": "custom"}).json()["presets"]["standard"]["blocks"] == original_custom
    assert client.post("/api/admin/dashboard-settings/mode", json={"mode": "recommended"}).json()["presets"]["recommended"]["blocks"] == original_recommended


def test_clock_mode_without_sensors_still_returns_date_and_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "dashboard" / "settings.json"
    SettingsRepository(path).save(DashboardSettings("clock", (), (), ()), {})
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(path))
    monkeypatch.setattr(dashboard_main, "_dashboard_candidates", lambda now=None: [])

    response = client.get("/api/display")

    assert response.status_code == 200
    assert response.json()["mode"] == "clock"
    assert response.json()["supplemental"] == []
    assert response.json()["updated_at"] == "--"
    assert response.json()["freshness"] == "unavailable"
    assert re.fullmatch(r"\d{2}:\d{2}", response.json()["time"])


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display UI tests")
def test_clock_polling_updates_existing_supplemental_slots_without_reordering() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function leaf() { return {textContent: "", hidden: false, className: "", dateTime: ""}; }
function reading() { const children = {"[data-role=\"label\"]": leaf(), "[data-role=\"value\"]": leaf(), "[data-role=\"unit\"]": leaf()}; return {className: "clock-reading clock-reading--unavailable", children, querySelector(selector) { return children[selector]; }}; }
const date = leaf(), time = leaf(), updated = leaf(), freshness = leaf(), temperature = reading(), grid = reading();
const readings = {temperature, grid};
global.document = {documentElement: {classList: {add() {}}}, querySelector(selector) {
  if (selector === "#clock-date") return date;
  if (selector === "#clock-time") return time;
  if (selector === "#clock-updated-at") return updated;
  if (selector === "#clock-freshness") return freshness;
  const match = selector.match(/^\[data-item-id=\"(.+)\"\]$/); return match ? readings[match[1]] : undefined;
}};
global.CSS = {escape: value => value};
global.window = {setInterval() {}};
global.fetch = async () => ({ok: true, json: async () => ({})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
updateDisplay({mode: "clock", date: "2026年8月19日 水曜日", time: "12:34", updated_at: "2026-08-19 12:34:00", updated_at_iso: "2026-08-19T12:34:00+09:00", freshness: "normal", supplemental: [
  {id: "temperature", short_label: "温度", value: "27.1", unit: "°C", freshness: "normal"},
  {id: "grid", short_label: "系統電力", value: "1.8", unit: "kW", freshness: "normal"},
]});
console.log(JSON.stringify({date: date.textContent, time: time.textContent, updated: updated.textContent, freshness: freshness, temperature: temperature.children, grid: grid.children, classes: [temperature.className, grid.className], ids: Object.keys(readings)}));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["date"] == "2026年8月19日 水曜日"
    assert result["time"] == "12:34"
    assert result["updated"] == "2026-08-19 12:34:00"
    assert result["freshness"]["className"] == "freshness freshness--normal"
    assert result["temperature"]["[data-role=\"label\"]"]["textContent"] == "温度"
    assert result["grid"]["[data-role=\"value\"]"]["textContent"] == "1.8"
    assert result["grid"]["[data-role=\"unit\"]"]["textContent"] == "kW"
    assert result["classes"] == ["clock-reading clock-reading--normal", "clock-reading clock-reading--normal"]
    assert result["ids"] == ["temperature", "grid"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
def test_legacy_polling_restores_delayed_power_and_sen66_status_to_normal() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function classList() { const values = new Set(); return {add: (...names) => names.forEach(name => values.add(name)), remove: (...names) => names.forEach(name => values.delete(name)), values: () => [...values].sort()}; }
function section() { return {classList: classList(), querySelectorAll() { return []; }}; }
const power = section(), sen66 = section();
const powerBadge = {textContent: "遅延", hidden: false, className: "source-badge source-badge--delayed"};
const sen66Badge = {textContent: "遅延", hidden: false, className: "source-badge source-badge--delayed"};
global.document = {documentElement: {classList: classList()}, querySelector(selector) {
  return ({"#power-section": power, "#sen66-section": sen66, "#power-source-badge": powerBadge, "#sen66-source-badge": sen66Badge})[selector] || null;
}};
let reloads = 0;
global.window = {setInterval() {}, location: {reload() { reloads += 1; }}};
global.fetch = async () => ({ok: true, json: async () => ({})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__displayTest = { updateDisplay };");
const snapshot = freshness => ({
  current_power_label: "現在の消費電力", current_power_kw: "1.20", power_direction: "", power_flow: "neutral",
  has_ichijo_power_flow: true, grid_flow_label: "", grid_flow_kw: "", grid_flow: "neutral", pv_power_kw: "0.80",
  sold_today_kwh: "0.0", battery_soc_percent: "50", battery_power_label: "", battery_power_kw: "", purchased_today_kwh: "0.0",
  temperature_c: "25.0", humidity_percent: "45", co2_ppm: "600", pm25_ug_m3: "2.0", voc_index: "10",
  updated_at: "2026/08/19 12:00:00", updated_at_iso: "2026-08-19T12:00:00+09:00", freshness,
  power_freshness: freshness, sen66_freshness: freshness,
});
__displayTest.updateDisplay(snapshot("delayed"));
const delayed = {power: power.classList.values(), sen66: sen66.classList.values(), powerBadge: {...powerBadge}, sen66Badge: {...sen66Badge}};
__displayTest.updateDisplay(snapshot("normal"));
console.log(JSON.stringify({delayed, normal: {power: power.classList.values(), sen66: sen66.classList.values(), powerBadge, sen66Badge}, reloads}));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["delayed"]["power"] == ["source--delayed"]
    assert result["delayed"]["sen66"] == ["source--delayed"]
    assert result["normal"]["power"] == ["source--normal"]
    assert result["normal"]["sen66"] == ["source--normal"]
    assert result["normal"]["powerBadge"] == {"textContent": "", "hidden": True, "className": "source-badge source-badge--normal"}
    assert result["normal"]["sen66Badge"] == {"textContent": "", "hidden": True, "className": "source-badge source-badge--normal"}
    assert result["reloads"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
def test_polling_reloads_when_server_switches_from_legacy_to_block_layout() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
global.document = {documentElement: {classList: {add() {}}}, querySelector(selector) { return selector === "#power-section" ? {} : null; }};
let reloads = 0;
global.window = {setInterval() {}, location: {reload() { reloads += 1; }}};
global.fetch = async () => ({ok: true, json: async () => ({})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__displayTest = { updateDisplay };");
__displayTest.updateDisplay({mode: "recommended", blocks: []});
console.log(JSON.stringify({reloads}));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)

    assert json.loads(completed.stdout) == {"reloads": 1}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
@pytest.mark.parametrize("mode", ["custom", "clock", "recommended"])
def test_demo_polling_keeps_selected_mode(mode: str) -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const mode = process.argv[2], intervals = [], urls = [], navigations = [];
global.document = {body: {dataset: {demoEnabled: "true", demoMode: mode, dashboardMode: mode}}, documentElement: {classList: {add() {}}}, querySelector() { return null; }};
global.window = {setInterval(fn, delay) { intervals.push({fn, delay}); }, location: {assign(url) { navigations.push(url); }, reload() { throw Error("unexpected reload"); }}};
global.fetch = async (url) => { urls.push(url); return {ok: true, json: async () => ({mode, blocks: [], supplemental: []})}; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  assert.deepStrictEqual(intervals.map(x => x.delay), [1000, 10000]);
  const poll = intervals.find(x => x.delay === 10000);
  for (let i = 0; i < 4; i++) await poll.fn();
  assert.deepStrictEqual(urls, Array(4).fill(`/api/display?demo_mode=${mode}`));
  assert.deepStrictEqual(navigations, []);
  updateDisplay({mode: "legacy"});
  assert.deepStrictEqual(navigations, [`/display?demo_mode=${mode}`]);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", harness, str(javascript_path), mode], check=True, capture_output=True, text=True)
    script = javascript_path.read_text(encoding="utf-8")
    for obsolete in ("demoModes", "demoModeIndex", "nextDemoMode", "rotation"):
        assert obsolete not in script
    user_html = "\n".join(
        (Path(__file__).parents[1] / "app" / "templates" / name).read_text(encoding="utf-8")
        for name in ("display.html", "admin_display.html")
    )
    assert "fixture" not in user_html.lower()
    assert "fixture" not in script.lower()


@pytest.mark.parametrize("saved_mode", ["clock", "recommended"])
@pytest.mark.parametrize("with_candidates", [False, True])
def test_demo_manual_modes_preserve_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_mode: str, with_candidates: bool) -> None:
    item = DisplayItem("temp", "温度", "th", "omk/th/environment", "th-demo", "temperature_c", "number", "℃", "環境", "temperature", True, "", "--", "unavailable")
    candidates = [item] if with_candidates else []
    monkeypatch.setattr(dashboard_main, "_dashboard_candidates", lambda now=None: candidates)
    path = tmp_path / "dashboard" / "settings.json"
    SettingsRepository(path).save(DashboardSettings(saved_mode, (), (), (), True), {})
    original = path.read_bytes()
    for query, expected in [("", "custom"), ("?demo_mode=custom", "custom"), ("?demo_mode=clock", "clock"), ("?demo_mode=recommended", "recommended")]:
        response = client.get("/display" + query)
        assert response.status_code == 200
        assert f'data-dashboard-mode="{expected}"' in response.text
        assert 'class="demo-mode-notice"' in response.text
        for mode, label in [("custom", "カスタム"), ("clock", "時計"), ("recommended", "おすすめ")]:
            current = ' aria-current="page"' if mode == expected else ""
            assert f'<a href="/display?demo_mode={mode}"{current}>{label}</a>' in response.text
        for _ in range(2):
            snapshot = client.get("/api/display" + query)
            assert snapshot.status_code == 200
            assert snapshot.json()["mode"] == expected
        assert path.read_bytes() == original
    for enabled in (False, True):
        assert client.post("/api/admin/dashboard-settings/demo", json={"enabled": enabled}).status_code == 200
        assert json.loads(path.read_text())["mode"] == saved_mode
        normal_mode = saved_mode if with_candidates or saved_mode == "clock" else None
        assert client.get("/api/display").json().get("mode") == ("custom" if enabled else normal_mode)
        for endpoint in ("/display", "/api/display"):
            assert client.get(endpoint + "?demo_mode=invalid").status_code == 400
        if not enabled:
            normal_html = client.get("/display").text
            for mode in ("custom", "clock", "recommended"):
                html = client.get(f"/display?demo_mode={mode}").text
                assert 'class="demo-mode-notice"' not in html
                assert re.search(r'data-dashboard-mode="[^"]*"', html).group() == re.search(r'data-dashboard-mode="[^"]*"', normal_html).group()
                assert client.get(f"/api/display?demo_mode={mode}").json().get("mode") == normal_mode
    assert 'data-dashboard-mode="custom"' in client.get("/display").text


def test_legacy_demo_rotation_setting_is_ignored_without_losing_presets(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    block = DisplayBlock("environment", "環境", "温度", "small", "temp", ("temp",))
    settings = DashboardSettings("clock", (block,), (block,), ("temp",), True)
    payload = settings.as_dict()
    payload["demo"]["rotation_seconds"] = 10
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = repository.load_or_create({"temp": "環境"}, [])
    assert loaded == settings
    repository.save(loaded, {"temp": "環境"})
    assert json.loads(path.read_text()) == settings.as_dict()
    assert "rotation_seconds" not in json.loads(path.read_text())["demo"]


@pytest.mark.parametrize("freshness", ["normal", "delayed", "stale", "unavailable"])
def test_demo_fallback_only_replaces_stale_or_unavailable(freshness: str) -> None:
    item = DisplayItem("temp", "温度", "th", "omk/th/environment", "th-demo", "temperature_c", "number", "℃", "環境", "temperature", True, "", "19.0", freshness)
    result = apply_demo_fallback([item])[0]
    assert item.value == "19.0"
    assert item.freshness == freshness
    assert item.source_kind == "real"
    assert result.as_dict()["source_kind"] == ("demo" if freshness in {"stale", "unavailable"} else "real")
    assert result.short_label == item.short_label
    if freshness in {"normal", "delayed"}:
        assert result is item
    else:
        assert result.value == "25.1"
        assert result.freshness == "normal"


def test_demo_indicator_is_fixed_and_display_assets_are_cache_busted() -> None:
    template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert re.search(r"display\.css'\) }}\?v=[^\"']+", template)
    assert re.search(r"display\.js'\) }}\?v=[^\"']+", template)
    notice = stylesheet.split(".demo-mode-notice {", 1)[1].split("}", 1)[0]
    assert "position: fixed;" in notice
    assert "margin: 0;" in notice


def test_demo_fixture_uses_anonymized_snapshots_and_keeps_sen66_and_switchbot_distinct() -> None:
    fixture = json.loads((Path(__file__).parents[1] / "app" / "demo" / "readings.json").read_text(encoding="utf-8"))
    values = fixture["values"]
    assert fixture["source"] == "Anonymized snapshots from actual OMK development measurements"
    assert fixture["captured_date"] == "2026-09-03"
    assert abs((float(values["pv_power"]) + float(values["battery_discharge"])) - (float(values["load_power"]) + float(values["grid_export"]))) <= 0.01

    sen66_pm1 = DisplayItem("pm1", "PM1.0", "sen66", "omk/sen66/environment", "sen66", "pm1_0_ug_m3", "number", "µg/m³", "環境", None, True, "", "--", "unavailable", short_label="PM1.0")
    switchbot_temperature = DisplayItem("temp", "温度", "th", "omk/th/environment", "th-demo", "temperature_c", "number", "℃", "環境", "temperature", True, "", "--", "unavailable", short_label="温度")
    result = apply_demo_fallback([sen66_pm1, switchbot_temperature])
    assert [item.value for item in result] == ["0.7", "25.1"]
    assert all(item.source_kind == "demo" for item in result)
    assert [item.short_label for item in result] == ["PM1.0", "温度"]


def _write_instantaneous_data(
    root: Path,
    *,
    power_at: datetime | None = None,
    sen66_at: datetime | None = None,
    ichijo_at: datetime | None = None,
) -> None:
    _write_latest(
        root,
        "broute_power.json",
        {
            "measured_at": (power_at or datetime(2026, 7, 30, 12, 0, 20, tzinfo=JST)).isoformat(),
            "net_power_w": -3581.0,
        },
    )
    _write_latest(
        root,
        "sen66.json",
        {
            "temperature_celsius": 26.44,
            "relative_humidity_percent": 48.4,
            "co2_ppm": 1200.0,
            "pm2_5_ug_m3": 20.2,
            "voc_index": 123.4,
        },
        sen66_at or datetime(2026, 7, 30, 12, 0, 0, tzinfo=JST),
    )
    if ichijo_at is not None:
        _write_latest(
            root,
            "ichijo_power_flow.json",
            {
                "measured_at": ichijo_at.isoformat(),
                "load_power_w": 1500.0,
                "pv_power_w": 800.0,
                "grid_import_power_w": 700.0,
                "grid_export_power_w": 0.0,
                "battery_soc_percent": 65.0,
                "battery_charge_power_w": 100.0,
                "battery_discharge_power_w": 0.0,
                "battery_operating_state": "charging",
            },
        )


def test_latest_repository_reads_instantaneous_values(tmp_path: Path) -> None:
    _write_instantaneous_data(tmp_path, ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST))

    repository = LatestRepository(tmp_path)
    power = repository.latest_power()
    sen66 = repository.latest_sen66()
    ichijo = repository.latest_ichijo_power_flow()

    assert power is not None and power.net_power_w == -3581.0
    assert sen66 is not None and sen66.measured_at == datetime(2026, 7, 30, 12, 0, tzinfo=JST)
    assert sen66.temperature_c == 26.44
    assert ichijo is not None and ichijo.load_power_w == 1500.0
    assert ichijo.battery_operating_state == "charging"


def test_view_model_uses_latest_values_and_parquet_today_energy(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    processed_root = tmp_path / "processed"
    _write_instantaneous_data(latest_root)
    _write_energy_data(processed_root)

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(processed_root), now=NOW)

    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_direction == "売電"
    assert dashboard.power_flow == "sale"
    assert dashboard.temperature_c == "26.4"
    assert dashboard.humidity_percent == "48"
    assert dashboard.co2_ppm == "1200"
    assert dashboard.pm25_ug_m3 == "20.2"
    assert dashboard.voc_index == "123"
    assert dashboard.purchased_today_kwh == "0.7"
    assert dashboard.sold_today_kwh == "1.3"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == "normal"
    assert dashboard.power_freshness == FreshnessStatus.NORMAL
    assert dashboard.sen66_freshness == FreshnessStatus.NORMAL


def test_view_model_displays_missing_today_energy_totals_as_dash(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(latest_root)

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.purchased_today_kwh == "--"
    assert dashboard.sold_today_kwh == "--"


def test_stale_ichijo_falls_back_to_broute_power(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(latest_root, ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST))

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_direction == "売電"


def test_delayed_ichijo_keeps_values_and_marks_power_source(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is True
    assert dashboard.current_power_kw == "1.50"
    assert dashboard.pv_power_kw == "0.80"
    assert dashboard.power_freshness == FreshnessStatus.DELAYED


def test_unavailable_ichijo_and_broute_hides_power_value(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "-"
    assert dashboard.power_freshness == FreshnessStatus.UNAVAILABLE


def test_delayed_broute_keeps_value_when_ichijo_is_unavailable(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_kw == "3.58"
    assert dashboard.power_freshness == FreshnessStatus.DELAYED


def test_sen66_delayed_and_unavailable_are_isolated_from_power(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 11, 53, 30, tzinfo=JST),
    )

    delayed = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)
    assert delayed.temperature_c == "26.4"
    assert delayed.sen66_freshness == FreshnessStatus.DELAYED
    assert delayed.current_power_kw == "1.50"

    _write_instantaneous_data(
        latest_root,
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )
    unavailable = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)
    assert unavailable.temperature_c == "-"
    assert unavailable.humidity_percent == "-"
    assert unavailable.voc_index == "-"
    assert unavailable.sen66_freshness == FreshnessStatus.UNAVAILABLE
    assert unavailable.current_power_kw == "1.50"


def test_fresh_ichijo_excludes_stale_broute_from_global_status(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 11, 0, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 12, 0, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 12, 0, 25, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is True
    assert dashboard.current_power_label == "現在の消費電力"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == FreshnessStatus.NORMAL


def test_stale_ichijo_uses_fresh_broute_for_global_status(tmp_path: Path) -> None:
    latest_root = tmp_path / "latest"
    _write_instantaneous_data(
        latest_root,
        power_at=datetime(2026, 7, 30, 12, 0, 20, tzinfo=JST),
        sen66_at=datetime(2026, 7, 30, 12, 0, tzinfo=JST),
        ichijo_at=datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST),
    )

    dashboard = get_display_view_model(LatestRepository(latest_root), ParquetRepository(tmp_path / "processed"), now=NOW)

    assert dashboard.has_ichijo_power_flow is False
    assert dashboard.current_power_label == "現在の売電"
    assert dashboard.updated_at == "2026/07/30 12:00:00"
    assert dashboard.updated_at_iso == "2026-07-30T12:00:00+09:00"
    assert dashboard.freshness == FreshnessStatus.NORMAL


def test_dashboard_timestamp_display_uses_seconds_without_timezone_metadata() -> None:
    assert format_timestamp_seconds("2026-08-17T18:48:40.315+09:00") == "2026-08-17 18:48:40"


def test_broken_or_missing_latest_data_does_not_break_display(tmp_path: Path, monkeypatch) -> None:
    latest_root = tmp_path / "latest"
    latest_root.mkdir()
    (latest_root / "broute_power.json").write_text("{broken", encoding="utf-8")
    item_id = _write_generic_item(
        latest_root,
        "b",
        topic="omk/living/environment",
        device_id="living",
        field="temperature_c",
        value=25.0,
        received_at=datetime.now(JST),
    )
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path / "processed"))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    monkeypatch.setattr(dashboard_main, "_DERIVED_ENERGY_CACHE", None)

    assert LatestRepository(latest_root).latest_power() is None
    response = client.get("/display")

    assert response.status_code == 200
    assert 'id="display-blocks"' in response.text
    assert f'data-item-id="{item_id}"' in response.text
    assert "display-card--" in response.text
    assert "Traceback" not in response.text


def test_display_api_handles_legacy_latest_files_with_recommended_blocks(tmp_path: Path, monkeypatch) -> None:
    latest_root = tmp_path / "latest"
    current_time = datetime.now(JST)
    _write_latest(
        latest_root,
        "broute_power.json",
        {"measured_at": current_time.isoformat(), "net_power_w": 1240.0},
        current_time,
    )
    _write_latest(
        latest_root,
        "sen66.json",
        {
            "temperature_celsius": 25.0,
            "relative_humidity_percent": 45.0,
            "co2_ppm": 600.0,
            "pm2_5_ug_m3": 3.0,
            "voc_index": 90.0,
        },
        current_time,
    )
    _write_latest(
        latest_root,
        "ichijo_power_flow.json",
        {
            "measured_at": current_time.isoformat(),
            "load_power_w": 1500.0,
            "pv_power_w": 800.0,
            "grid_import_power_w": 700.0,
            "grid_export_power_w": 0.0,
            "battery_soc_percent": 65.0,
            "battery_charge_power_w": 100.0,
            "battery_discharge_power_w": 0.0,
            "battery_operating_state": "charging",
        },
        current_time,
    )
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_PROCESSED_DATA_ROOT", str(tmp_path / "processed"))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    monkeypatch.setattr(dashboard_main, "_DERIVED_ENERGY_CACHE", None)

    response = client.get("/api/display")

    assert response.status_code == 200
    snapshot = response.json()
    # No settings file represents a new OMK, which defaults to recommended.
    assert snapshot["mode"] == "recommended"
    assert isinstance(snapshot["blocks"], list)
    assert snapshot["freshness"] in {"normal", "delayed", "unavailable"}
    assert "has_ichijo_power_flow" not in snapshot

    normal_html = client.get("/display").text
    assert 'id="display-blocks"' in normal_html
    assert "Traceback" not in normal_html
    assert 'id="current-power-kw"' not in normal_html


def test_hero_display_html_and_javascript_expose_polling_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    item_ids = [
        _write_generic_item(
            latest_root, "h", topic="omk/living/environment", device_id="living",
            field="temperature_c", value=25.4, received_at=datetime.now(JST),
        ),
        _write_generic_item(
            latest_root, "i", topic="omk/living/environment", device_id="living",
            field="relative_humidity_percent", value=48.0, received_at=datetime.now(JST),
        ),
        _write_generic_item(
            latest_root, "j", topic="omk/living/environment", device_id="living",
            field="co2_ppm", value=650, received_at=datetime.now(JST),
        ),
    ]
    settings_path = tmp_path / "dashboard" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(_block_payload({
        "block_id": "block_sen66", "group": "living", "title": "SEN66", "size": "large", "layout_pattern": "hero",
        "primary_item_id": item_ids[0], "item_ids": item_ids,
    })), encoding="utf-8")
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(settings_path))
    response = client.get("/display")
    javascript = (Path(__file__).parents[1] / "app" / "static" / "display.js").read_text(encoding="utf-8")
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    for element_id in ("header-date-main", "header-weekday", "header-time", "updated-at", "freshness", "display-blocks"):
        assert f'id="{element_id}"' in response.text
    assert 'class="admin-link"' in response.text
    assert '<svg viewBox="0 0 24 24"' in response.text
    assert 'data-block-id="block_sen66"' in response.text
    for item_id in item_ids:
        assert f'data-item-id="{item_id}"' in response.text
    assert "display-card--large" in response.text
    assert "display-card--hero" in response.text
    assert 'data-item-count="3"' in response.text
    assert 'class="display-card-primary"' in response.text
    assert 'class="display-card-secondary"' in response.text
    assert 'class="display-secondary-label"' in response.text
    assert 'class="display-secondary-value"' in response.text
    assert 'class="display-secondary-unit"' in response.text
    assert 'fetch("/api/display", { cache: "no-store" })' in javascript
    assert "DISPLAY_POLL_INTERVAL_MS = 10_000" in javascript
    assert "headerWeekday.textContent" in javascript
    assert 'WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]' in javascript
    assert 'data.mode === "clock"' in javascript
    assert '["standard", "custom", "recommended"].includes(data.mode)' in javascript
    assert "Array.isArray(data.blocks)" in javascript
    assert "data-item-id" in javascript
    assert "item.short_label || item.label" in javascript
    assert ".standard-grid" in stylesheet
    assert ".display-card--large" in stylesheet
    assert ".display-card--medium" in stylesheet
    assert ".display-card--small" in stylesheet
    assert ".display-card--hero" in stylesheet
    assert ".display-card--strip" in stylesheet
    assert re.search(r"\.display-card--hero \{[^}]*grid-template-columns:", stylesheet)
    assert ".display-card--hero .display-card-primary { grid-column: 1; grid-row: 2;" in stylesheet
    assert ".display-card--hero .display-card-secondary { grid-column: 2; grid-row: 2;" in stylesheet
    assert ".admin-link svg { width: clamp(28px, 3vw, 36px);" in stylesheet
    assert "font-variant-numeric: tabular-nums" in stylesheet


def test_strip_and_compact_display_html_use_their_own_dom_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    strip_ids = [
        _write_generic_item(latest_root, suffix, topic="omk/sen66/environment", device_id="sen66", field=field, value=value, received_at=datetime.now(JST))
        for suffix, field, value in (("k", "temperature_c", 25.4), ("l", "relative_humidity_percent", 48.0), ("m", "co2_ppm", 650))
    ]
    compact_ids = [
        _write_generic_item(latest_root, suffix, topic="omk/plug-001/status", device_id="plug-001", field=field, value=value, received_at=datetime.now(JST))
        for suffix, field, value in (("n", "power_w", 12.3), ("o", "switch_state", True), ("p", "metric_a", 1), ("q", "metric_b", 2), ("r", "metric_c", 3))
    ]
    settings_path = tmp_path / "dashboard" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps(_block_payload(
        {"block_id": "strip_sen66", "group": "sen66", "title": "SEN66", "size": "medium", "layout_pattern": "strip", "primary_item_id": strip_ids[0], "item_ids": strip_ids},
        {"block_id": "compact_plug", "group": "plug-001", "title": "Plug", "size": "medium", "layout_pattern": "compact", "primary_item_id": compact_ids[0], "item_ids": compact_ids},
    )), encoding="utf-8")
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(settings_path))

    response = client.get("/display")

    assert response.status_code == 200
    assert 'data-block-id="strip_sen66"' in response.text
    assert "display-card--medium" in response.text
    assert "display-card--strip" in response.text
    assert 'data-item-count="3"' in response.text
    assert 'class="display-card-strip-items"' in response.text
    for item_id in strip_ids:
        assert f'data-item-id="{item_id}"' in response.text
    assert 'data-block-id="compact_plug"' in response.text
    assert "display-card--medium" in response.text
    assert "display-card--compact" in response.text
    assert 'data-item-count="5"' in response.text
    for item_id in compact_ids:
        assert f'data-item-id="{item_id}"' in response.text


def test_strip_initial_render_keeps_an_empty_unit_element_for_voc_index() -> None:
    temperature = SimpleNamespace(id="temperature", label="温度", short_label="温度", value="25.4", unit="°C", freshness="normal")
    voc = SimpleNamespace(id="voc", label="VOC Index", short_label="VOC Index", value="440", unit="", freshness="normal")
    block = SimpleNamespace(id="strip_sen66", title="SEN66", size="medium", layout_pattern="strip", freshness="normal", primary=temperature, secondary=[voc])
    dashboard = SimpleNamespace(blocks=[block], updated_at="2026-08-19 12:00:00", updated_at_iso="2026-08-19T12:00:00+09:00", freshness="normal")
    request = SimpleNamespace(url_for=lambda _name, **params: params["path"])
    response = dashboard_main.templates.get_template("display.html").render(request=request, dashboard=dashboard)

    voc_markup = re.search(
        r'<div class="display-secondary-item" data-item-id="voc">(.*?)</div>', response, re.DOTALL,
    )
    assert voc_markup is not None
    assert '<small class="display-secondary-unit" data-role="unit">\xa0</small>' in voc_markup.group(1)


def test_block_initial_render_keeps_units_for_recovery_from_unavailable() -> None:
    item = lambda item_id, unit: SimpleNamespace(id=item_id, label=item_id, short_label=item_id, value="--", unit=unit, freshness="unavailable")
    hero_primary, hero_secondary = item("hero-primary", "kW"), item("hero-secondary", "°C")
    compact_primary, compact_secondary = item("compact-primary", "W"), item("compact-secondary", "%")
    unitless = item("unitless", "")
    blocks = [
        SimpleNamespace(id="hero", title="Hero", size="large", layout_pattern="hero", freshness="unavailable", primary=hero_primary, secondary=[hero_secondary], auxiliary_supported=False),
        SimpleNamespace(id="compact", title="Compact", size="medium", layout_pattern="compact", freshness="unavailable", primary=compact_primary, secondary=[compact_secondary, unitless]),
        SimpleNamespace(id="strip", title="Strip", size="small", layout_pattern="strip", freshness="unavailable", primary=item("strip-primary", "ppm"), secondary=[]),
    ]
    dashboard = SimpleNamespace(blocks=blocks, updated_at="--", updated_at_iso="", freshness="unavailable")
    request = SimpleNamespace(url_for=lambda _name, **params: params["path"])
    response = dashboard_main.templates.get_template("display.html").render(request=request, dashboard=dashboard)

    assert '<div class="display-card-primary" data-item-id="hero-primary">' in response
    assert '<span class="display-card-unit" data-role="unit" hidden>kW</span>' in response
    assert '<small class="display-secondary-unit" data-role="unit">\xa0</small>' in response
    assert '<small class="display-secondary-unit" data-role="unit" hidden>W</small>' in response
    assert '<small class="display-secondary-unit" data-role="unit" hidden>%</small>' in response
    unitless_markup = re.search(r'<div class="display-secondary-item" data-item-id="unitless">(.*?)</div>', response, re.DOTALL)
    assert unitless_markup is not None
    assert 'data-role="unit"' not in unitless_markup.group(1)
    assert '<small class="display-secondary-unit" data-role="unit">\xa0</small>' in response


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
def test_strip_polling_keeps_unitless_item_unit_row_and_dom_contract() -> None:
    """A unitless VOC item must retain its third strip row after every poll."""
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function classList() {
  const values = new Set();
  return { add: (...names) => names.forEach(name => values.add(name)), remove: (...names) => names.forEach(name => values.delete(name)), values: () => [...values].sort() };
}
function roleElement() { return {textContent: "", hidden: false}; }
function itemCard() {
  const roles = {label: roleElement(), value: roleElement(), unit: roleElement()};
  roles.unit.textContent = "\u00a0";
  return {classList: classList(), roles, querySelector(selector) { const match = selector.match(/data-role="([^"]+)"/); return match ? roles[match[1]] : null; }};
}
const cards = {temperature: itemCard(), voc: itemCard()};
const blockFreshness = roleElement();
const block = {classList: classList(), querySelector(selector) { return selector === '[data-role="freshness"]' ? blockFreshness : null; }};
const byId = {"#current-datetime": null, "#header-date-main": null, "#header-weekday": null, "#header-time": null, "#updated-at": null, "#freshness": null};
global.CSS = {escape: value => value};
global.document = {documentElement: {classList: classList()}, querySelector(selector) {
  if (selector.startsWith('[data-block-id=')) return block;
  if (selector.startsWith('[data-item-id=')) return cards[selector.match(/"([^"]+)"/)[1]] || null;
  return byId[selector] || null;
}};
global.window = {setInterval() {}};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__displayTest = { updateDisplay };");
const item = (id, label, value, unit, freshness) => ({id, short_label: label, label, value, unit, freshness});
const snapshot = (vocValue, vocFreshness) => ({mode: "custom", blocks: [{id: "sen66", freshness: "normal", primary: item("temperature", "温度", "27.2", "°C", "normal"), secondary: [item("voc", "VOC Index", vocValue, "", vocFreshness)]}], updated_at: "", updated_at_iso: "", freshness: "normal"});
const vocUnit = cards.voc.roles.unit;
const states = [];
const record = () => states.push({value: cards.voc.roles.value.textContent, unit: cards.voc.roles.unit.textContent, unitHidden: cards.voc.roles.unit.hidden, classes: cards.voc.classList.values()});
__displayTest.updateDisplay(snapshot("452", "normal"));
record();
__displayTest.updateDisplay(snapshot("440", "normal"));
record();
__displayTest.updateDisplay(snapshot("--", "unavailable"));
record();
__displayTest.updateDisplay(snapshot("440", "normal"));
record();
console.log(JSON.stringify({
  voc: {label: cards.voc.roles.label.textContent, value: cards.voc.roles.value.textContent, unit: cards.voc.roles.unit.textContent, unitHidden: cards.voc.roles.unit.hidden, unitRetained: vocUnit === cards.voc.roles.unit, roleNames: Object.keys(cards.voc.roles).sort(), classes: cards.voc.classList.values()},
  temperature: {unit: cards.temperature.roles.unit.textContent, unitHidden: cards.temperature.roles.unit.hidden},
  states,
}));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result["voc"] == {
        "label": "VOC Index",
        "value": "440",
        "unit": "\u00a0",
        "unitHidden": False,
        "unitRetained": True,
        "roleNames": ["label", "unit", "value"],
        "classes": ["display-card--normal"],
    }
    assert result["temperature"] == {"unit": "°C", "unitHidden": False}
    assert result["states"] == [
        {"value": "452", "unit": "\u00a0", "unitHidden": False, "classes": ["display-card--normal"]},
        {"value": "440", "unit": "\u00a0", "unitHidden": False, "classes": ["display-card--normal"]},
        {"value": "--", "unit": "\u00a0", "unitHidden": False, "classes": ["display-card--unavailable"]},
        {"value": "440", "unit": "\u00a0", "unitHidden": False, "classes": ["display-card--normal"]},
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
def test_block_polling_updates_card_freshness_badge_and_values() -> None:
    """The card-level badge must clear after a recovered block without affecting item updates."""
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function classList() {
  const values = new Set();
  return {add: (...names) => names.forEach(name => values.add(name)), remove: (...names) => names.forEach(name => values.delete(name)), values: () => [...values].sort()};
}
function leaf() { return {textContent: "", hidden: false}; }
function itemCard() {
  const roles = {label: leaf(), value: leaf(), unit: leaf()};
  return {classList: classList(), querySelector(selector) { const match = selector.match(/data-role="([^"]+)"/); return match ? roles[match[1]] : null; }, roles};
}
const item = itemCard(), badge = leaf();
const block = {classList: classList(), querySelector(selector) { return selector === '[data-role="freshness"]' ? badge : null; }};
const byId = {"#current-datetime": null, "#header-date-main": null, "#header-weekday": null, "#header-time": null, "#updated-at": leaf(), "#freshness": leaf()};
global.CSS = {escape: value => value};
global.document = {documentElement: {classList: classList()}, querySelector(selector) {
  if (selector.startsWith('[data-block-id=')) return block;
  if (selector.startsWith('[data-item-id=')) return item;
  return byId[selector] || null;
}};
global.window = {setInterval() {}};
global.fetch = async () => ({ok: true, json: async () => ({})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__displayTest = { updateDisplay };");
const snapshot = (freshness, value) => ({mode: "custom", freshness, updated_at: "2026/08/20 12:00:00", updated_at_iso: "2026-08-20T12:00:00+09:00", blocks: [{id: "power", freshness, primary: {id: "power-item", short_label: "消費電力", label: "消費電力", value, unit: "kW", freshness}, secondary: []}]});
const states = [];
for (const [freshness, value] of [["delayed", "1.20"], ["normal", "1.30"], ["unavailable", "--"], ["normal", "1.40"], ["delayed", "1.50"], ["unavailable", "--"]]) {
  __displayTest.updateDisplay(snapshot(freshness, value));
  states.push({freshness, badge: badge.textContent, value: item.roles.value.textContent, cardClasses: block.classList.values(), itemClasses: item.classList.values()});
}
console.log(JSON.stringify(states));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == [
        {"freshness": "delayed", "badge": "遅延", "value": "1.20", "cardClasses": ["display-card--delayed"], "itemClasses": ["display-card--delayed"]},
        {"freshness": "normal", "badge": "", "value": "1.30", "cardClasses": ["display-card--normal"], "itemClasses": ["display-card--normal"]},
        {"freshness": "unavailable", "badge": "取得不可", "value": "--", "cardClasses": ["display-card--unavailable"], "itemClasses": ["display-card--unavailable"]},
        {"freshness": "normal", "badge": "", "value": "1.40", "cardClasses": ["display-card--normal"], "itemClasses": ["display-card--normal"]},
        {"freshness": "delayed", "badge": "遅延", "value": "1.50", "cardClasses": ["display-card--delayed"], "itemClasses": ["display-card--delayed"]},
        {"freshness": "unavailable", "badge": "取得不可", "value": "--", "cardClasses": ["display-card--unavailable"], "itemClasses": ["display-card--unavailable"]},
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
def test_block_polling_restores_units_for_all_layout_patterns() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function classList() {
  const values = new Set();
  return {add: (...names) => names.forEach(name => values.add(name)), remove: (...names) => names.forEach(name => values.delete(name)), values: () => [...values].sort()};
}
function leaf(hidden = false) { return {textContent: "", hidden}; }
function itemCard(unit) {
  const roles = {label: leaf(), value: leaf()};
  if (unit !== null) roles.unit = leaf(true);
  return {classList: classList(), roles, querySelector(selector) { const match = selector.match(/data-role="([^"]+)"/); return match ? roles[match[1]] : null; }};
}
function block() { const badge = leaf(); return {classList: classList(), badge, querySelector(selector) { return selector === '[data-role="freshness"]' ? badge : null; }}; }
const items = {heroPrimary: itemCard("kW"), heroSecondary: itemCard("°C"), compactPrimary: itemCard("W"), stripPrimary: itemCard("ppm"), unitless: itemCard(null)};
const blocks = {hero: block(), compact: block(), strip: block()};
const byId = {"#current-datetime": null, "#header-date-main": null, "#header-weekday": null, "#header-time": null, "#updated-at": leaf(), "#freshness": leaf()};
global.CSS = {escape: value => value};
global.document = {documentElement: {classList: classList()}, querySelector(selector) {
  if (selector.startsWith('[data-block-id=')) return blocks[selector.match(/"([^"]+)"/)[1]];
  if (selector.startsWith('[data-item-id=')) return items[selector.match(/"([^"]+)"/)[1]];
  return byId[selector] || null;
}};
global.window = {setInterval() {}};
global.fetch = async () => ({ok: true, json: async () => ({})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.__displayTest = { updateDisplay };");
const item = (id, value, unit, freshness) => ({id, short_label: id, label: id, value, unit, freshness});
const snapshot = freshness => ({mode: "custom", freshness, updated_at: "", updated_at_iso: "", blocks: [
  {id: "hero", freshness, primary: item("heroPrimary", freshness === "unavailable" ? "--" : "1.42", "kW", freshness), secondary: [item("heroSecondary", freshness === "unavailable" ? "--" : "26.4", "°C", freshness)]},
  {id: "compact", freshness, primary: item("compactPrimary", freshness === "unavailable" ? "--" : "120", "W", freshness), secondary: [item("unitless", freshness === "unavailable" ? "--" : "440", "", freshness)]},
  {id: "strip", freshness, primary: item("stripPrimary", freshness === "unavailable" ? "--" : "620", "ppm", freshness), secondary: []},
]});
for (const freshness of ["unavailable", "normal", "delayed", "normal"]) __displayTest.updateDisplay(snapshot(freshness));
const result = {};
for (const [id, card] of Object.entries(items)) result[id] = {value: card.roles.value.textContent, unit: card.roles.unit ? card.roles.unit.textContent : null, unitHidden: card.roles.unit ? card.roles.unit.hidden : null};
for (const [id, card] of Object.entries(blocks)) result[id] = {badge: card.badge.textContent, classes: card.classList.values()};
console.log(JSON.stringify(result));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {
        "heroPrimary": {"value": "1.42", "unit": "kW", "unitHidden": False},
        "heroSecondary": {"value": "26.4", "unit": "°C", "unitHidden": False},
        "compactPrimary": {"value": "120", "unit": "W", "unitHidden": False},
        "stripPrimary": {"value": "620", "unit": "ppm", "unitHidden": False},
        "unitless": {"value": "440", "unit": None, "unitHidden": None},
        "hero": {"badge": "", "classes": ["display-card--normal"]},
        "compact": {"badge": "", "classes": ["display-card--normal"]},
        "strip": {"badge": "", "classes": ["display-card--normal"]},
    }


def test_power_direction_rules() -> None:
    assert format_power(LatestPower(NOW, 1240.0)) == ("1.24", "買電", PowerDirection.PURCHASE)
    assert format_power(LatestPower(NOW, 0.0)) == ("0.00", "収支なし", PowerDirection.NEUTRAL)


def test_freshness_status_boundaries() -> None:
    assert freshness_for(NOW, NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 11, 54, 30, tzinfo=JST), NOW) == FreshnessStatus.NORMAL
    assert freshness_for(datetime(2026, 7, 30, 11, 54, 29, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 11, 50, 30, tzinfo=JST), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(datetime(2026, 7, 30, 11, 50, 29, tzinfo=JST), NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(None, NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(datetime(2026, 7, 30, 12, 1, tzinfo=JST), NOW) == FreshnessStatus.NORMAL


def test_periodic_energy_freshness_uses_its_30_minute_cadence() -> None:
    interval = 30 * 60
    # Instantaneous power keeps its existing short freshness window.
    assert freshness_for(NOW - timedelta(minutes=7), NOW) == FreshnessStatus.DELAYED
    assert freshness_for(NOW - timedelta(minutes=11), NOW) == FreshnessStatus.UNAVAILABLE
    assert freshness_for(NOW - timedelta(minutes=44, seconds=59), NOW, interval) == FreshnessStatus.NORMAL
    assert freshness_for(NOW - timedelta(minutes=45, seconds=1), NOW, interval) == FreshnessStatus.DELAYED
    assert freshness_for(NOW - timedelta(minutes=89, seconds=59), NOW, interval) == FreshnessStatus.DELAYED
    assert freshness_for(NOW - timedelta(minutes=90, seconds=1), NOW, interval) == FreshnessStatus.UNAVAILABLE


def test_broute_energy_candidates_keep_30_minute_values_fresh(tmp_path: Path) -> None:
    grid = _write_generic_item(tmp_path, "g", topic="omk/broute/power", device_id="broute", field="net_power_w", value=100, received_at=NOW - timedelta(minutes=7))
    imported = _write_generic_item(tmp_path, "i", topic="omk/broute/energy", device_id="broute", field="import_energy_kwh", value=1.2, received_at=NOW - timedelta(minutes=40))
    candidates = {item.id: item for item in display_candidates(DisplayRepository(tmp_path), NOW)}

    assert candidates[grid].freshness == FreshnessStatus.DELAYED
    assert candidates[imported].freshness == FreshnessStatus.NORMAL
    assert definition_for("import_energy_kwh").expected_update_interval_seconds == 30 * 60


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_dashboard_display() -> None:
    redirect = client.get("/", follow_redirects=False)

    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/display"

    display = client.get("/")
    assert display.status_code == 200
    assert "<title>OMK ダッシュボード</title>" in display.text


def test_generic_catalog_candidates_group_and_format_values(tmp_path: Path) -> None:
    broute_id = _write_generic_item(tmp_path, "a", topic="omk/broute-001/power", device_id="broute-001", field="net_power_w", value=1234)
    plug_id = _write_generic_item(tmp_path, "b", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=12.3)
    ichijo_id = _write_generic_item(tmp_path, "c", topic="omk/ichijo-001/power-flow", device_id="ichijo-001", field="pv_power_w", value=800)
    status_id = _write_generic_item(tmp_path, "d", topic="omk/broute-001/status", device_id="broute-001", field="status", value="online", value_type="string")
    repository = DisplayRepository(tmp_path)
    candidates = {item.id: candidate_for(item) for item in repository.catalog()}

    assert repository.item(broute_id) is not None
    assert candidates[broute_id].group == "電力メーター（Bルート）"
    assert candidates[plug_id].group == "plug-001"
    assert candidates[ichijo_id].group == "パワコン"
    assert candidates[status_id].selectable is False
    assert definition_for("unknown_scalar").selectable is True
    assert definition_for("net_power_w").semantic_role == "grid_power"
    assert definition_for("net_power_w").unit == "kW"
    assert definition_for("load_power_w").label == "住宅内消費電力"
    assert format_value(1103, definition_for("load_power_w")) == "1.10"
    assert format_value(23, definition_for("net_power_w")) == "0.02"
    assert definition_for("power_w").semantic_role == "device_power"
    assert definition_for("power_w").unit == "W"
    assert format_value(4.1, definition_for("power_w")) == "4.1"
    assert format_value(True, definition_for("switch_state")) == "ON"
    display_candidates = {item.id: item for item in catalog_items_with_latest(repository, NOW)}
    assert (display_candidates[broute_id].value, display_candidates[broute_id].unit) == ("1.23", "kW")
    assert (display_candidates[plug_id].value, display_candidates[plug_id].unit) == ("12.3", "W")


def test_settings_validate_capacity_duplicates_and_persist_atomically(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    groups = {"item_a": "source_a", "item_b": "source_a", "item_c": "source_b"}
    settings = repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "large", "primary_item_id": "item_a", "item_ids": ["item_a", "item_b"]}), groups)
    assert settings.blocks[0].item_ids == ("item_a", "item_b")
    assert settings.blocks[0].layout_pattern == "hero"
    assert repository.load_or_create(groups, []).blocks == settings.blocks
    assert stat.S_IMODE((tmp_path / "dashboard" / "settings.json").stat().st_mode) == 0o644
    assert not list((tmp_path / "dashboard").glob("*.tmp"))
    with pytest.raises(SettingsError, match="複数ブロック"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "primary_item_id": "item_a", "item_ids": ["item_a"]}, {"block_id": "b", "group": "source_a", "title": "B", "size": "small", "primary_item_id": "item_a", "item_ids": ["item_a"]}), groups)
    with pytest.raises(SettingsError, match="存在しない"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "primary_item_id": "missing", "item_ids": ["missing"]}), groups)
    with pytest.raises(SettingsError, match="表示領域"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "large", "primary_item_id": "item_a", "item_ids": ["item_a"]}, {"block_id": "b", "group": "source_b", "title": "B", "size": "large", "primary_item_id": "item_c", "item_ids": ["item_c"]}, {"block_id": "c", "group": "source_a", "title": "C", "size": "small", "primary_item_id": "item_b", "item_ids": ["item_b"]}), groups)
    with pytest.raises(SettingsError):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "huge", "primary_item_id": "item_a", "item_ids": ["item_a"]}), groups)
    with pytest.raises(SettingsError, match="項目数"):
        repository.save_payload(_block_payload({"block_id": "a", "group": "source_a", "title": "A", "size": "small", "layout_pattern": "compact", "primary_item_id": "item_a", "item_ids": ["item_a", "item_b", "item_c", "item_d"]}), {"item_a": "source_a", "item_b": "source_a", "item_c": "source_a", "item_d": "source_a"})


def test_catalog_candidates_include_the_same_latest_value_and_freshness_as_display(tmp_path: Path) -> None:
    item_id = _write_generic_item(tmp_path, "g", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=22.0, received_at=NOW)
    repository = DisplayRepository(tmp_path)
    candidates = {item.id: item for item in catalog_items_with_latest(repository, NOW)}
    selected = selected_items(repository, (DisplaySelection(item_id, "small"),), NOW)

    assert candidates[item_id].value == "22.0"
    assert candidates[item_id].freshness == "normal"
    assert selected[0].value == candidates[item_id].value
    assert selected[0].freshness == candidates[item_id].freshness


def test_v2_layout_pattern_is_saved_and_existing_v2_defaults_by_size(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    groups = {"temp": "SEN66", "humidity": "SEN66"}
    settings = repository.save_payload(_block_payload({
        "block_id": "sen", "group": "SEN66", "title": "SEN66", "size": "medium",
        "layout_pattern": "strip", "primary_item_id": "temp", "item_ids": ["temp", "humidity"],
    }), groups)
    assert settings.blocks[0].layout_pattern == "strip"
    assert json.loads((tmp_path / "dashboard" / "settings.json").read_text(encoding="utf-8"))["presets"]["standard"]["blocks"][0]["layout_pattern"] == "strip"

    old_v2 = _block_payload({"block_id": "old", "group": "SEN66", "title": "SEN66", "size": "large", "primary_item_id": "temp", "item_ids": ["temp"]})
    assert repository._parse(old_v2, groups, allow_missing=False)[0].blocks[0].layout_pattern == "hero"


def test_item_limits_depend_on_size_and_pattern(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    ids = [f"item_{index}" for index in range(6)]
    groups = {item_id: "SEN66" for item_id in ids}
    medium = repository.save_payload(_block_payload({
        "block_id": "sen", "group": "SEN66", "title": "SEN66", "size": "medium",
        "layout_pattern": "compact", "primary_item_id": ids[0], "item_ids": ids[:5],
    }), groups)
    assert len(medium.blocks[0].item_ids) == 5
    with pytest.raises(SettingsError, match="項目数"):
        repository.save_payload(_block_payload({
            "block_id": "small", "group": "SEN66", "title": "SEN66", "size": "small",
            "layout_pattern": "compact", "primary_item_id": ids[0], "item_ids": ids[:4],
        }), groups)


@pytest.mark.parametrize("sizes", [("large", "small", "small", "small"), ("large", "medium"), ("medium", "medium", "medium"), ("medium", "medium", "small", "small")])
def test_capacity_valid_grid_combinations_fit_the_block_budget(tmp_path: Path, sizes: tuple[str, ...]) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    groups = {f"item_{index}": f"source_{index}" for index in range(len(sizes))}
    blocks = [
        {"block_id": f"block_{index}", "group": groups[f"item_{index}"], "title": f"Block {index}", "size": size,
         "layout_pattern": "hero" if size == "large" else "compact", "primary_item_id": f"item_{index}", "item_ids": [f"item_{index}"]}
        for index, size in enumerate(sizes)
    ]
    assert len(repository.save_payload(_block_payload(*blocks), groups).blocks) == len(sizes)


def test_battery_virtual_item_replaces_raw_candidates_and_migrates_settings(tmp_path: Path) -> None:
    charge = _write_generic_item(tmp_path, "b", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_charge_power_w", value=0, received_at=NOW)
    discharge = _write_generic_item(tmp_path, "c", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_discharge_power_w", value=0, received_at=NOW)
    state = _write_generic_item(tmp_path, "s", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_operating_state", value="standby", value_type="string", received_at=NOW)
    candidates = display_candidates(DisplayRepository(tmp_path), NOW)
    battery = next(item for item in candidates if item.semantic_role == "battery_power_bidirectional")
    battery_state = next(item for item in candidates if item.id == state)

    assert battery.id == "virtual:battery_power_bidirectional:ichijo"
    assert battery.label.endswith("蓄電池充放電")
    assert battery.short_label == "蓄電池充放電"
    assert battery_state.short_label == "蓄電池状態"
    assert all(item.short_label != "蓄電池 待機" for item in candidates)
    assert {item.id for item in candidates}.isdisjoint({charge, discharge})

    path = tmp_path / "dashboard" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_block_payload({
        "block_id": "ichijo", "group": "パワコン", "title": "パワコン", "size": "small",
        "layout_pattern": "compact", "primary_item_id": charge, "item_ids": [charge, discharge],
    })), encoding="utf-8")
    settings = SettingsRepository(path).load_or_create(
        {battery.id: battery.group}, [], display_item_migrations(candidates),
    )
    assert settings.blocks[0].primary_item_id == battery.id
    assert settings.blocks[0].item_ids == (battery.id,)
    assert json.loads(path.read_text(encoding="utf-8"))["presets"]["standard"]["blocks"][0]["item_ids"] == [battery.id]


def test_v1_settings_migrate_to_grouped_blocks(tmp_path: Path) -> None:
    path = tmp_path / "dashboard" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "default_preset": "standard", "presets": {"standard": {"items": [
        {"item_id": "temp", "size": "small"}, {"item_id": "humidity", "size": "small"}, {"item_id": "grid", "size": "large"},
    ]}}}), encoding="utf-8")
    settings = SettingsRepository(path).load_or_create({"temp": "SEN66", "humidity": "SEN66", "grid": "電力メーター（Bルート）"}, [])

    assert [(block.group, block.item_ids, block.primary_item_id) for block in settings.blocks] == [
        ("SEN66", ("temp", "humidity"), "temp"), ("電力メーター（Bルート）", ("grid",), "grid"),
    ]
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 3
    assert migrated["mode"] == "custom"
    assert migrated["presets"]["standard"]["blocks"] == [block.as_dict() for block in settings.blocks]


def test_display_block_resolves_primary_and_multiple_secondary_values(tmp_path: Path) -> None:
    item_ids = [
        _write_generic_item(tmp_path, "v", topic="omk/sen66/environment", device_id="sen66", field="temperature_c", value=26.8, received_at=NOW),
        _write_generic_item(tmp_path, "w", topic="omk/sen66/environment", device_id="sen66", field="relative_humidity_percent", value=40, received_at=NOW),
        _write_generic_item(tmp_path, "x", topic="omk/sen66/environment", device_id="sen66", field="co2_ppm", value=520, received_at=NOW),
    ]
    block = DisplayBlock("sen66", "sen66", "SEN66", "medium", item_ids[0], tuple(item_ids), "strip")
    rendered = selected_blocks(DisplayRepository(tmp_path), (block,), NOW)

    assert rendered[0].primary.value == "26.8"
    assert [item.value for item in rendered[0].secondary] == ["40", "520"]
    assert [item.short_label for item in (rendered[0].primary, *rendered[0].secondary)] == ["温度", "湿度", "CO₂濃度"]
    assert rendered[0].secondary[1].label == "sen66 CO₂濃度"
    assert (rendered[0].secondary[1].id, rendered[0].secondary[1].field, rendered[0].secondary[1].semantic_role) == (item_ids[2], "co2_ppm", "co2")
    assert rendered[0].as_dict()["size"] == "medium"
    assert rendered[0].as_dict()["layout_pattern"] == "strip"


def test_energy_blocks_keep_multiple_broute_and_ichijo_values(tmp_path: Path) -> None:
    grid = _write_generic_item(tmp_path, "y", topic="omk/broute/power", device_id="broute", field="net_power_w", value=770, received_at=NOW)
    imported = _write_generic_item(tmp_path, "z", topic="omk/broute/energy", device_id="broute", field="import_energy_kwh", value=2.5, received_at=NOW)
    load = _write_generic_item(tmp_path, "0", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1103, received_at=NOW)
    pv = _write_generic_item(tmp_path, "1", topic="omk/ichijo/power-flow", device_id="ichijo", field="pv_power_w", value=1210, received_at=NOW)
    repository = DisplayRepository(tmp_path)
    blocks = selected_blocks(repository, (
        DisplayBlock("broute", "電力メーター（Bルート）", "Bルート", "small", grid, (grid, imported)),
        DisplayBlock("ichijo", "パワコン", "パワコン", "large", load, (load, pv)),
    ), NOW)

    assert blocks[0].primary.unit == "kW" and blocks[0].secondary[0].unit == "kWh"
    assert (blocks[1].primary.value, blocks[1].secondary[0].value) == ("1.10", "1.21")


def test_small_plug_block_uses_full_consumption_label_without_ellipsis(tmp_path: Path) -> None:
    plug = _write_generic_item(tmp_path, "p", topic="omk/plug-001/power", device_id="plug-001", field="power_w", value=12.3, received_at=NOW)
    block = DisplayBlock("plug", "plug-001", "plug-001", "small", plug, (plug,), "compact")
    rendered = selected_blocks(DisplayRepository(tmp_path), (block,), NOW)[0]
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")
    template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")

    assert rendered.primary.short_label == "消費電力"
    assert "{{ reading_label(block.primary) }}" in template
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-card-compact-items { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }" in stylesheet
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-item { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); width: 100%; min-width: 0; }" in stylesheet
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-label { width: 100%; min-width: 0; overflow: visible; text-overflow: clip; white-space: nowrap; }" in stylesheet
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-compact-reading { width: 100%; min-width: 0; }" in stylesheet
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-item { grid-column: 1 / -1; }" in stylesheet
    assert ".display-card--small.display-card--stacked {" in stylesheet
    assert re.search(r"display\.css'\) }}\?v=[^\"']+", template)
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-label { overflow: visible; text-overflow: clip; white-space: nowrap; }" in stylesheet


def test_power_flow_default_group_title_is_rendered_as_ichijo_power_conditioner(tmp_path: Path) -> None:
    load = _write_generic_item(tmp_path, "t", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1103, received_at=NOW)
    block = DisplayBlock("ichijo", "パワコン", "パワコン", "large", load, (load,), "hero")

    assert selected_blocks(DisplayRepository(tmp_path), (block,), NOW)[0].title == "パワコン"


def test_derived_daily_energy_candidates_reuse_legacy_totals_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeParquet:
        calls = 0
        def today_energy_totals(self, _today):
            self.calls += 1
            return dashboard_main.EnergyTotals(1.2, 3.4)
    fake = FakeParquet()
    monkeypatch.setattr(dashboard_main, "_DERIVED_ENERGY_CACHE", None)
    monkeypatch.setattr(dashboard_main, "display_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(dashboard_main, "get_display_repository", lambda: object())
    monkeypatch.setattr(dashboard_main, "get_parquet_repository", lambda: fake)
    candidates = dashboard_main._dashboard_candidates(NOW)
    again = dashboard_main._dashboard_candidates(NOW + timedelta(seconds=10))

    assert [(item.id, item.value, item.unit, item.group) for item in candidates] == [
        ("derived:energy:today_import_kwh", "1.2", "kWh", "パワコン"),
        ("derived:energy:today_export_kwh", "3.4", "kWh", "パワコン"),
    ]
    assert [(item.short_label, item.semantic_role) for item in candidates] == [
        ("本日の買電量", "today_import_energy"), ("本日の売電量", "today_export_energy"),
    ]
    assert [item.id for item in again] == [item.id for item in candidates]
    assert fake.calls == 1


def test_derived_daily_energy_candidates_show_missing_data_as_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeParquet:
        def today_energy_totals(self, _today):
            return dashboard_main.EnergyTotals(None, None)
    monkeypatch.setattr(dashboard_main, "_DERIVED_ENERGY_CACHE", None)
    monkeypatch.setattr(dashboard_main, "display_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(dashboard_main, "get_display_repository", lambda: object())
    monkeypatch.setattr(dashboard_main, "get_parquet_repository", lambda: FakeParquet())
    candidates = dashboard_main._dashboard_candidates(NOW)
    assert [(item.value, item.freshness) for item in candidates] == [("--", "unavailable"), ("--", "unavailable")]


def test_new_ichijo_block_accepts_raw_virtual_and_derived_ids(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    ids = ("load", "pv", "export", "soc", "virtual:battery_power_bidirectional:ichijo", "derived:energy:today_import_kwh")
    groups = {item_id: "パワコン" for item_id in ids}
    settings = repository.save_payload(_block_payload({"block_id": "ichijo-new", "group": "パワコン", "title": "パワコン", "size": "large", "layout_pattern": "hero", "primary_item_id": "load", "item_ids": list(ids)}), groups)
    assert settings.blocks[0].item_ids == ids


def test_ichijo_charge_and_discharge_are_one_dashboard_only_battery_row(tmp_path: Path) -> None:
    load = _write_generic_item(tmp_path, "a", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1103, received_at=NOW)
    pv = _write_generic_item(tmp_path, "b", topic="omk/ichijo/power-flow", device_id="ichijo", field="pv_power_w", value=520, received_at=NOW)
    charge = _write_generic_item(tmp_path, "c", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_charge_power_w", value=0, received_at=NOW)
    discharge = _write_generic_item(tmp_path, "d", topic="omk/ichijo/power-flow", device_id="ichijo", field="battery_discharge_power_w", value=590, received_at=NOW)
    grid = _write_generic_item(tmp_path, "e", topic="omk/ichijo/power-flow", device_id="ichijo", field="grid_import_power_w", value=0, received_at=NOW)
    repository = DisplayRepository(tmp_path)
    battery_id = next(item.id for item in display_candidates(repository, NOW) if item.semantic_role == "battery_power_bidirectional")
    block = DisplayBlock("ichijo", "パワコン", "パワコン", "large", load, (load, pv, battery_id, grid), "hero")
    rendered = selected_blocks(repository, (block,), NOW)[0]

    assert rendered.layout_pattern == "hero"
    assert rendered.primary.short_label == "住宅内消費電力"
    assert [(item.semantic_role, item.short_label, item.value) for item in rendered.secondary] == [
        ("pv_power", "PV発電", "0.52"),
        ("battery_power_bidirectional", "蓄電池充放電", "0.59"),
        ("grid_import", "買電電力", "0.00"),
    ]


def test_ichijo_load_hero_exposes_legacy_grid_flow_as_auxiliary_status(tmp_path: Path) -> None:
    def render(root: Path, *, imported: float, exported: float, layout: str = "hero"):
        load = _write_generic_item(root, "l", topic="omk/ichijo/power-flow", device_id="ichijo", field="load_power_w", value=1234, received_at=NOW)
        imported_id = _write_generic_item(root, "i", topic="omk/ichijo/power-flow", device_id="ichijo", field="grid_import_power_w", value=imported, received_at=NOW)
        _write_generic_item(root, "e", topic="omk/ichijo/power-flow", device_id="ichijo", field="grid_export_power_w", value=exported, received_at=NOW)
        repository = DisplayRepository(root)
        candidates = display_candidates(repository, NOW)
        block = DisplayBlock("ichijo", "パワコン", "パワコン", "large", load, (load, imported_id), layout)
        return selected_blocks(repository, (block,), NOW, candidates)[0]

    imported = render(tmp_path / "import", imported=420, exported=0)
    exported = render(tmp_path / "export", imported=420, exported=3150)
    neutral = render(tmp_path / "neutral", imported=0, exported=0)
    non_hero = render(tmp_path / "compact", imported=420, exported=0, layout="compact")

    assert (imported.auxiliary_supported, imported.auxiliary_label, imported.auxiliary_value, imported.auxiliary_unit, imported.auxiliary_flow) == (True, "買電中", "0.42", "kW", "purchase")
    assert (exported.auxiliary_label, exported.auxiliary_value, exported.auxiliary_flow) == ("売電中", "3.15", "sale")
    assert (neutral.auxiliary_label, neutral.auxiliary_value) == ("", "")
    assert len(imported.secondary) == 1
    assert non_hero.auxiliary_supported is False
    template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")
    assert 'class="display-card-auxiliary display-card-auxiliary--{{ block.auxiliary_flow }}" data-role="auxiliary"' in template
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")
    assert ".display-card-auxiliary--purchase" in stylesheet
    assert ".display-card-auxiliary--sale" in stylesheet


def test_display_items_api_lists_multiple_values_from_one_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    broute_fields = ("net_power_w", "cumulative_energy_import_kwh", "cumulative_energy_export_kwh")
    for suffix, field in zip(("m", "n", "o"), broute_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/broute-001/power", device_id="broute-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    ichijo_fields = ("pv_power_w", "load_power_w", "battery_soc_percent")
    for suffix, field in zip(("p", "q", "r"), ichijo_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/ichijo-001/power-flow", device_id="ichijo-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    sen66_fields = ("temperature_c", "relative_humidity_percent", "co2_ppm")
    for suffix, field in zip(("s", "t", "u"), sen66_fields, strict=True):
        _write_generic_item(
            latest_root, suffix, topic="omk/sen66-001/environment", device_id="sen66-001",
            field=field, value=10, received_at=datetime.now(JST),
        )
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))

    response = client.get("/api/admin/display-items")

    assert response.status_code == 200
    groups = {group["name"]: group["items"] for group in response.json()["groups"]}
    broute = groups["電力メーター（Bルート）"]
    assert {item["field"] for item in broute} == set(broute_fields)
    assert all(item["selectable"] for item in broute)
    ichijo = groups["パワコン"]
    assert set(ichijo_fields) <= {item["field"] for item in ichijo}
    assert {item["field"]: item["short_label"] for item in ichijo if item["field"] in ichijo_fields} == {
        "pv_power_w": "PV発電", "load_power_w": "住宅内消費電力", "battery_soc_percent": "蓄電池残量",
    }
    derived = {item["id"]: item for item in ichijo if item["id"].startswith("derived:energy:")}
    for item_id, field in (
        ("derived:energy:today_import_kwh", "today_import_kwh"),
        ("derived:energy:today_export_kwh", "today_export_kwh"),
    ):
        assert derived[item_id]["field"] == field
        assert derived[item_id]["group"] == "パワコン"
        assert derived[item_id]["unit"] == "kWh"
        assert derived[item_id]["selectable"] is True
    assert derived["derived:energy:today_import_kwh"]["short_label"] == "本日の買電量"
    assert derived["derived:energy:today_export_kwh"]["short_label"] == "本日の売電量"
    assert {item["field"] for item in groups["sen66-001"]} == set(sen66_fields)


def test_admin_display_css_allows_vertical_scroll_without_changing_kiosk_overflow() -> None:
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")
    admin_template = (Path(__file__).parents[1] / "app" / "templates" / "admin_display.html").read_text(encoding="utf-8")
    display_template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")

    assert "html.admin-document { height: auto; min-height: 100%; overflow-x: hidden; overflow-y: scroll;" in stylesheet
    assert "html.admin-document body.admin-body { min-height: 100vh; min-height: 100dvh; height: auto; overflow: visible; }" in stylesheet
    assert "html.admin-document::-webkit-scrollbar { width: 12px; }" in stylesheet
    assert ".display-settings-page { width: min(100%, 1180px); height: auto; min-height: 100dvh; max-height: none;" in stylesheet
    assert ".display-settings-page { padding: 14px; }" in stylesheet
    assert ".display-settings-intro { display: grid; grid-template-columns: minmax(0, 1fr) auto;" in stylesheet
    assert "min-height: 46px;" in stylesheet
    assert '<html lang="ja" class="admin-document">' in admin_template
    assert '<body class="admin-body">' in admin_template
    assert '保存してダッシュボードを確認' in admin_template
    assert 'href="/display">ダッシュボードを確認</a>' not in admin_template
    assert '>保存する<' not in admin_template
    assert re.search(r"admin_display\.js'\) }}\?v=20260819-clock-navigation-\d+", admin_template)
    assert re.search(r"display\.css'\) }}\?v=20260910-demo-layout-\d+", admin_template)
    assert '<body class="admin-body">' not in display_template
    assert re.search(r"display\.js'\) }}\?v=[^\"']+", display_template)
    assert re.search(r"display\.css'\) }}\?v=[^\"']+", display_template)
    assert 'data-mode="clock"' in admin_template
    assert 'id="clock-summary"' in admin_template
    assert "mode !== \"clock\"" in (Path(__file__).parents[1] / "app" / "static" / "admin_display.js").read_text(encoding="utf-8")
    assert "dashboard.mode is defined and dashboard.mode == 'clock'" in display_template
    assert "clock-dashboard" in display_template
    assert 'id="clock-updated-at"' in display_template
    assert 'id="clock-freshness"' in display_template
    assert 'href="/admin"' in display_template
    assert 'href="/admin/display"' not in display_template
    admin_javascript = (Path(__file__).parents[1] / "app" / "static" / "admin_display.js").read_text(encoding="utf-8")
    assert 'window.location.assign("/display")' in admin_javascript
    assert 'mode, default_preset: "standard"' in admin_javascript
    assert ".display-settings-page:has(#recommended-summary:not([hidden])) .display-settings-actions #save-settings" not in stylesheet
    assert ".clock-status-area" in stylesheet
    assert "font-size: clamp(176px, 24vw, 360px);" in stylesheet
    assert ".clock-reading + .clock-reading { border-left:" in stylesheet
    assert 'class="clock-reading-value"' in display_template
    assert ".clock-reading-value { display: inline-flex;" in stylesheet
    assert ".clock-reading { display: grid; grid-template-columns: minmax(0, 1fr); align-items: baseline; justify-items: center;" in stylesheet
    assert ".clock-reading-value { display: inline-flex; align-items: baseline; justify-self: center;" in stylesheet
    assert 'data-role="unit"' in display_template
    assert "overflow: hidden;" in stylesheet


def test_admin_display_keeps_demo_settings_after_normal_display_settings() -> None:
    template = (Path(__file__).parents[1] / "app" / "templates" / "admin_display.html").read_text(encoding="utf-8")
    demo_script = (Path(__file__).parents[1] / "app" / "static" / "demo_settings.js").read_text(encoding="utf-8")

    mode_buttons = [template.index(f'data-mode="{mode}"') for mode in ("recommended", "custom", "clock")]
    assert mode_buttons == sorted(mode_buttons)
    assert 'class="display-settings-main"' in template
    assert 'class="demo-settings"' in template
    assert template.index('id="save-settings"') < template.index('class="demo-settings"')
    assert template.index('id="demo-enabled"') > template.index('id="save-settings"')
    assert '展示・説明時の利用を想定した機能です。' in template
    assert 'querySelector("#demo-enabled")' in demo_script
    assert '"/api/admin/dashboard-settings/demo"' in demo_script


def test_display_pattern_css_keeps_only_hero_primary_large_and_fits_the_viewport() -> None:
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")
    template = (Path(__file__).parents[1] / "app" / "templates" / "display.html").read_text(encoding="utf-8")

    assert ".standard-dashboard { height: 100vh; height: 100dvh; min-height: 0;" in stylesheet
    assert ".standard-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); grid-auto-rows: minmax(0, 1fr);" in stylesheet
    assert ".display-card--large { grid-column: span 12;" in stylesheet
    assert ".display-card--medium { grid-column: span 8;" in stylesheet
    assert ".display-card--small { grid-column: span 4;" in stylesheet
    assert ".display-card--hero .display-card-primary .display-card-reading strong" in stylesheet
    assert ".display-card--hero .display-card-label { padding-bottom:" in stylesheet
    assert ".display-card--large.display-card--hero .display-card-primary { display: flex; flex-direction: column;" in stylesheet
    assert ".display-card--large.display-card--hero .display-card-primary .display-card-reading" in stylesheet
    assert ".display-card--large.display-card--hero .display-card-primary .display-card-auxiliary" in stylesheet
    assert ".display-card--compact { grid-template-rows: auto minmax(0, 1fr) auto;" in stylesheet
    assert ".display-card-compact-items { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));" in stylesheet
    assert ".display-secondary-value { justify-self: end;" in stylesheet
    assert ".display-secondary-unit { min-height: 0;" in stylesheet
    assert ".display-card--large.display-card--hero" in stylesheet
    assert ".display-card--medium.display-card--hero" in stylesheet
    assert ".display-card--small.display-card--hero" in stylesheet
    assert ".display-card--medium.display-card--strip.display-card--items-" in stylesheet
    assert ".display-card--small.display-card--strip.display-card--items-" in stylesheet
    assert ".display-card--strip .display-secondary-unit { display: block; min-height: 1em;" in stylesheet
    assert ".display-compact-reading { display: flex; align-items: baseline;" in stylesheet
    assert "{% elif block.layout_pattern == 'compact' %}" in template
    assert 'class="display-card-compact-items"' in template
    assert '{{ reading_label(block.primary) }}' in template
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-item { grid-column: 1 / -1; }" in stylesheet
    assert ".display-card--small.display-card--compact.display-card--items-1 .display-secondary-label { overflow: visible; text-overflow: clip; white-space: nowrap; }" in stylesheet


def test_dynamic_display_api_uses_selected_order_and_keeps_unavailable_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    latest_root = tmp_path / "latest"
    fresh_id = _write_generic_item(latest_root, "e", topic="omk/living/environment", device_id="living", field="temperature_c", value=25.4, received_at=datetime.now(JST))
    old_id = _write_generic_item(latest_root, "f", topic="omk/bedroom/environment", device_id="bedroom", field="temperature_c", value=23.0, received_at=datetime.now(JST) - timedelta(seconds=601))
    monkeypatch.setenv("OMK_LATEST_DATA_ROOT", str(latest_root))
    monkeypatch.setenv("OMK_DASHBOARD_SETTINGS_PATH", str(tmp_path / "dashboard" / "settings.json"))
    put = client.put("/api/admin/dashboard-settings", json=_block_payload(
        {"block_id": "old", "group": "bedroom", "title": "寝室", "size": "small", "primary_item_id": old_id, "item_ids": [old_id]},
        {"block_id": "fresh", "group": "living", "title": "SEN66", "size": "large", "primary_item_id": fresh_id, "item_ids": [fresh_id]},
    ))
    assert put.status_code == 200
    snapshot = client.get("/api/display").json()
    assert snapshot["mode"] == "custom"
    assert [block["id"] for block in snapshot["blocks"]] == ["old", "fresh"]
    assert snapshot["blocks"][0]["primary"]["value"] == "--"
    assert snapshot["blocks"][0]["primary"]["freshness"] == "unavailable"
    assert snapshot["blocks"][1]["size"] == "large"


def test_delete_sensor_proxy_forwards_device_key_and_propagates_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def successful_request(method: str, path: str, body: dict | None = None) -> dict:
        calls.append((method, path))
        assert body is None
        return {"deleted": True, "device_key": "switchbot:plug", "sensor_id": "plug-001"}

    monkeypatch.setattr(dashboard_main, "_ble_request", successful_request)
    assert asyncio.run(dashboard_main.delete_sensor("switchbot:plug")) == {"deleted": True, "device_key": "switchbot:plug", "sensor_id": "plug-001"}
    assert calls == [("DELETE", "/api/sensors/switchbot:plug")]

    async def missing_request(_method: str, _path: str, _body: dict | None = None) -> dict:
        raise dashboard_main.HTTPException(404, "sensor was not found")

    monkeypatch.setattr(dashboard_main, "_ble_request", missing_request)
    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main.delete_sensor("switchbot:missing"))
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    ("status", "payload", "text", "expected"),
    [
        (404, {"detail": "node was not found"}, "", "node was not found"),
        (422, {"detail": [{"loc": ["body", "logical_id"], "msg": "invalid value"}]}, "", "logical_id: invalid value"),
        (500, ValueError(), "BLE manager unavailable", "BLE manager unavailable"),
        (500, ValueError(), "", "BLE manager error (HTTP 500)"),
    ],
)
def test_ble_proxy_preserves_status_and_safely_formats_backend_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: object, text: str, expected: str,
) -> None:
    class ErrorResponse:
        status_code = status

        def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

        @property
        def text(self) -> str:
            return text

    class ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, *_: object, **__: object):
            return ErrorResponse()

    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", lambda **_: ErrorClient())
    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._ble_request("POST", "/api/nodes/x/register", {"logical_id": "bad"}))
    assert error.value.status_code == status
    assert error.value.detail == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_display_settings_ui_shows_all_source_items_and_marks_selected() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin_display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {innerHTML: "", textContent: "", disabled: false, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, closest() { return null; }}; }
const elements = Object.fromEntries(["#selected-items", "#available-items", "#capacity-status", "#settings-status", "#save-settings"].map(key => [key, element()]));
global.document = {querySelector: selector => elements[selector]};
global.fetch = async url => ({ok: true, json: async () => url.endsWith("display-items") ? {
  capacity: 6,
  groups: [
    {name: "電力メーター（Bルート）", items: [
      {id: "grid", label: "Bルート 系統電力", short_label: "系統電力", group: "電力メーター（Bルート）", selectable: true},
      {id: "import-total", label: "Bルート 買電積算", short_label: "買電積算", group: "電力メーター（Bルート）", selectable: true},
      {id: "export-total", label: "Bルート 売電積算", short_label: "売電積算", group: "電力メーター（Bルート）", selectable: true},
    ]},
    {name: "パワコン", items: [
      {id: "load", field: "load_power_w", label: "パワコン 住宅内消費電力", short_label: "住宅内消費電力", group: "パワコン", selectable: true},
      {id: "pv", label: "パワコン PV発電", short_label: "PV発電", group: "パワコン", selectable: true},
      {id: "soc", label: "パワコン 蓄電池残量", short_label: "蓄電池残量", group: "パワコン", selectable: true},
    ]},
    {name: "SEN66", items: [
      {id: "temperature", label: "sen66 温度", short_label: "温度", group: "SEN66", selectable: true},
      {id: "humidity", label: "sen66 湿度", short_label: "湿度", group: "SEN66", selectable: true},
      {id: "co2", label: "sen66 CO₂濃度", short_label: "CO₂濃度", group: "SEN66", selectable: true},
    ]},
    {name: "plug-001", items: [
      {id: "plug-power", label: "plug-001 消費電力", short_label: "消費電力", group: "plug-001", selectable: true},
      {id: "plug-state", label: "plug-001 状態", short_label: "状態", group: "plug-001", selectable: true},
    ]},
    {name: "th-002", items: [{id: "th-temperature", label: "th-002 温度", short_label: "温度", group: "th-002", selectable: true}]},
  ],
} : {presets: {standard: {blocks: [
  {block_id: "b", group: "電力メーター（Bルート）", title: "Bルート", size: "large", primary_item_id: "grid", item_ids: ["grid"]},
  {block_id: "p", group: "パワコン", title: "パワコン", size: "small", primary_item_id: "load", item_ids: ["load"]},
  {block_id: "s", group: "SEN66", title: "SEN66", size: "small", primary_item_id: "temperature", item_ids: ["temperature"]},
]}}}});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setImmediate(() => {
  const changeMembership = (blockId, itemId, checked) => elements["#selected-items"].listeners.change({target: {dataset: {membership: `${blockId}:${itemId}`}, checked}});
  changeMembership("p", "pv", true);
  changeMembership("s", "humidity", true);
  changeMembership("s", "co2", true);
  const availableWithSpace = elements["#available-items"].innerHTML;
  elements["#available-items"].listeners.click({target: {closest() { return {dataset: {addBlock: "plug-001"}}; }}});
  const availableAtCapacity = elements["#available-items"].innerHTML;
  console.log(JSON.stringify({selected: elements["#selected-items"].innerHTML, availableWithSpace, availableAtCapacity, status: elements["#settings-status"].textContent}));
});
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)

    assert all(group in result["availableWithSpace"] for group in ("電力メーター（Bルート）", "パワコン", "SEN66", "th-002"))
    assert all(label in result["availableWithSpace"] for label in (
        "系統電力", "買電積算", "売電積算", "住宅内消費電力", "PV発電", "蓄電池残量",
        "温度", "湿度", "CO₂濃度",
    ))
    assert "パワコン PV発電" not in result["availableWithSpace"]
    assert "th-002 温度" not in result["availableAtCapacity"]
    assert "このデータでブロックを追加" in result["availableWithSpace"]
    assert "PV発電" in result["selected"]
    assert "CO₂濃度" in result["selected"]
    assert "表示形式" in result["selected"]
    assert "均等に並べる" in result["selected"]
    assert "このブロックに表示する値（3 / 3）" in result["selected"]
    assert "消費電力" in result["selected"]
    assert "温度" in result["availableAtCapacity"]
    assert "表示領域がいっぱいです" in result["availableAtCapacity"]
    assert result["status"] == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_display_settings_ui_defaults_ichijo_primary_and_keeps_save_feedback_compact() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin_display.js"
    template_path = Path(__file__).parents[1] / "app" / "templates" / "admin_display.html"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {innerHTML: "", textContent: "", disabled: false, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, closest() { return null; }}; }
const elements = Object.fromEntries(["#selected-items", "#available-items", "#capacity-status", "#settings-status", "#save-settings"].map(key => [key, element()]));
let savedPayload, redirect = null;
global.document = {querySelector: selector => elements[selector]};
global.window = {location: {assign(path) { redirect = path; }}};
global.fetch = async (url, options = {}) => {
  if (options.method === "PUT") { savedPayload = JSON.parse(options.body); return {ok: true, json: async () => ({})}; }
  return {ok: true, json: async () => url.endsWith("display-items") ? {capacity: 6, groups: [{name: "パワコン", items: [
    {id: "pv", field: "pv_power_w", label: "パワコン PV発電", short_label: "PV発電", group: "パワコン", selectable: true},
    {id: "load", field: "load_power_w", label: "パワコン 住宅内消費電力", short_label: "住宅内消費電力", group: "パワコン", selectable: true},
  ]}]} : {version: 3, mode: "custom", presets: {standard: {blocks: []}, recommended: {blocks: []}, clock: {item_ids: []}}}};
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setImmediate(async () => {
  elements["#available-items"].listeners.click({target: {closest() { return {dataset: {addBlock: "パワコン"}}; }}});
  await elements["#save-settings"].listeners.click();
  console.log(JSON.stringify({selected: elements["#selected-items"].innerHTML, saved: savedPayload, status: elements["#settings-status"].textContent, redirect}));
});
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    block = result["saved"]["presets"]["standard"]["blocks"][0]
    assert result["saved"]["mode"] == "custom"
    assert block["primary_item_id"] == "load"
    assert block["item_ids"] == ["load"]
    assert "住宅内消費電力" in result["selected"]
    assert 'option value="load" selected' in result["selected"]
    assert result["redirect"] == "/display"
    assert result["status"] == ""
    template = template_path.read_text(encoding="utf-8")
    assert 'id="save-settings" class="primary">保存してダッシュボードを確認</button>' in template
    assert 'class="dashboard-check-link"' not in template


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_display_settings_save_failure_keeps_the_editor_open() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin_display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {innerHTML: "", textContent: "", disabled: false, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, closest() { return null; }}; }
const elements = Object.fromEntries(["#selected-items", "#available-items", "#capacity-status", "#settings-status", "#save-settings"].map(key => [key, element()]));
let redirect = null;
global.document = {querySelector: selector => elements[selector]};
global.window = {location: {assign(path) { redirect = path; }}};
global.fetch = async (url, options = {}) => options.method === "PUT" ? {ok: false, json: async () => ({detail: "保存できません"})} : {ok: true, json: async () => url.endsWith("display-items") ? {capacity: 6, groups: []} : {presets: {standard: {blocks: []}}}};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setImmediate(async () => { await elements["#save-settings"].listeners.click(); console.log(JSON.stringify({redirect, status: elements["#settings-status"].textContent, disabled: elements["#save-settings"].disabled})); });
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)

    assert result == {"redirect": None, "status": "保存できません", "disabled": False}


def test_existing_ichijo_block_primary_is_preserved(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "dashboard" / "settings.json")
    settings = repository.save_payload(
        _block_payload({
            "block_id": "ichijo", "group": "パワコン", "title": "パワコン", "size": "small",
            "layout_pattern": "compact", "primary_item_id": "pv", "item_ids": ["pv", "load"],
        }),
        {"pv": "パワコン", "load": "パワコン"},
    )

    assert settings.blocks[0].primary_item_id == "pv"
    assert settings.blocks[0].item_ids == ("pv", "load")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_display_settings_ui_recalculates_size_pattern_item_limits() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin_display.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {innerHTML: "", textContent: "", disabled: false, listeners: {}, addEventListener(type, handler) { this.listeners[type] = handler; }, closest() { return null; }}; }
const elements = Object.fromEntries(["#selected-items", "#available-items", "#capacity-status", "#settings-status", "#save-settings"].map(key => [key, element()]));
global.document = {querySelector: selector => elements[selector]};
global.fetch = async url => ({ok: true, json: async () => url.endsWith("display-items") ? {capacity: 6, groups: [{name: "SEN66", items: ["temp", "humidity", "co2", "pm25", "voc"].map(id => ({id, label: `sen66 ${id}`, group: "SEN66", selectable: true}))}]} : {presets: {standard: {blocks: [{block_id: "sen", group: "SEN66", title: "SEN66", size: "small", layout_pattern: "compact", primary_item_id: "temp", item_ids: ["temp", "humidity", "co2"]}]}}}});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
setImmediate(() => {
  elements["#selected-items"].listeners.change({target: {dataset: {size: "sen"}, value: "medium"}});
  elements["#selected-items"].listeners.change({target: {dataset: {membership: "sen:pm25"}, checked: true}});
  elements["#selected-items"].listeners.change({target: {dataset: {membership: "sen:voc"}, checked: true}});
  console.log(elements["#selected-items"].innerHTML);
});
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)

    assert "このブロックに表示する値（5 / 5）" in completed.stdout
    assert "最大5項目" in completed.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_node_registration_ui_formats_errors_and_renders_state_transitions() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return { hidden: false, textContent: "", className: "", value: "", disabled: false, onclick: null, onsubmit: null, addEventListener() {}, closest() { return null; }, querySelector() { return element(); }, querySelectorAll() { return []; }, focus() { global.document.activeElement = this; }, setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }, showModal() {}, close() {} }; }
const selectors = ["#setup-status", "#candidates", "#registered-sensors", "#omk-nodes", "#start-scan", "#stop-scan", "#register-dialog", "#register-form", "#register-error", "#edit-dialog", "#edit-form", "#edit-error", "#cancel-register", "#cancel-edit", "#delete-sensor"];
const elements = Object.fromEntries(selectors.map(key => [key, element()]));
global.document = {activeElement: null, querySelector: selector => elements[selector] || element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: [], stage: "idle"})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
usbCandidatesInitialized = true;
const provisioned = nodeCard({node_id: "112233445566", registration_state: "provisioned", capabilities: ["ble_scan"], attached_sensors: ["SEN66"]});
const requested = nodeCard({node_id: "112233445566", registration_state: "provisioned", request_state: "request_sent", capabilities: ["ble_scan"], attached_sensors: ["SEN66"]});
const registeredMarkup = nodeCard({node_id: "112233445566", registration_state: "registered", logical_id: "sen66-001", capabilities: ["ble_scan", "sen66"], attached_sensors: ["SEN66"], relay_active: true, online: true});
const relayMarkup = nodeCard({node_id: "9af9509eb8b6", registration_state: "provisioned", capabilities: ["ble_scan", "sen66"], connected_sensors: [], attached_sensors: [], relay_active: true, online: true});
const offlineSEN66Markup = nodeCard({node_id: "9af9509eb8b7", registration_state: "provisioned", attached_sensors: ["SEN66"], online: false});
usbCandidatesInitialized = false;
const initialOfflineMarkup = nodeCard({node_id: "9af9509eb8b9", registration_state: "registered", logical_id: "old-sen66", connected_sensors: ["sen66"], online: false});
usbCandidatesInitialized = true;
usbCandidatesByNodeId.set("9af9509eb8b8", {node_id: "9af9509eb8b8", kind: "unconfirmed_esp32s3", device: "/dev/ttyACM0"});
const unconfirmedMarkup = nodeCard({node_id: "9af9509eb8b8", registration_state: "registered", logical_id: "old-sen66", connected_sensors: ["SEN66"], online: false});
  console.log(JSON.stringify({
  string: formatApiError("bad request"),
  object: formatApiError({loc: ["body", "logical_id"], msg: "invalid value"}),
  list: formatApiError([{loc: ["body", "logical_id"], msg: "invalid value"}]),
  provisioned: provisioned.includes("Logical IDを登録") && provisioned.includes("SEN66（検出情報あり）") && !provisioned.includes('<input class="node-logical-id"'),
  requested: requested.includes("Logical IDの登録要求を送信しました。Nodeの状態更新を待っています。") && !requested.includes("Logical IDを登録"),
  registered: registeredMarkup.includes("登録済み") && registeredMarkup.includes("sen66-001") && registeredMarkup.includes("logical-id-value") && registeredMarkup.includes("Logical IDを変更") && registeredMarkup.includes("Logical ID登録を解除") && !registeredMarkup.includes('<input class="node-logical-id"') && registeredMarkup.includes("SEN66（検出済み）") && registeredMarkup.includes("BLE relay: 稼働中"),
  relay: relayMarkup.includes("SEN66対応") && relayMarkup.includes("接続センサ: 未検出") && relayMarkup.includes("SEN66を使用しないNodeではLogical ID登録は不要です。") && relayMarkup.includes("BLE relay: 稼働中") && !relayMarkup.includes('<input class="node-logical-id"'),
  offlineSEN66: offlineSEN66Markup.includes("SEN66（検出情報あり）") && offlineSEN66Markup.includes("Logical IDを登録"),
  initialOffline: initialOfflineMarkup.includes("接続センサ: 確認中") && !initialOfflineMarkup.includes("old-sen66") && !initialOfflineMarkup.includes('<input class="node-logical-id"'),
  unconfirmed: unconfirmedMarkup.includes("接続センサ: セットアップ後に確認") && !unconfirmedMarkup.includes("old-sen66") && !unconfirmedMarkup.includes('<input class="node-logical-id"'),
  logicalIdValidation: !validLogicalId("") && !validLogicalId("bad id") && validLogicalId("ble-relay-001"),
  }));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {
        "string": "bad request",
        "object": "logical_id: invalid value",
        "list": "logical_id: invalid value",
        "provisioned": True,
        "requested": True,
        "registered": True,
        "relay": True,
        "offlineSEN66": True,
        "initialOffline": True,
        "unconfirmed": True,
        "logicalIdValidation": True,
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for admin UI tests")
def test_sensor_management_ui_renders_presence_sensor_values_and_safe_missing_values() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {hidden: false, textContent: "", className: "", value: "", onclick: null, onsubmit: null, closest() { return null; }, querySelector() { return element(); }, querySelectorAll() { return []; }, focus() {}, showModal() {}, close() {}}; }
const selectors = ["#setup-status", "#candidates", "#registered-sensors", "#omk-nodes", "#start-scan", "#stop-scan", "#register-dialog", "#register-form", "#register-error", "#edit-dialog", "#edit-form", "#edit-error", "#cancel-register", "#cancel-edit", "#delete-sensor"];
const elements = Object.fromEntries(selectors.map(key => [key, element()]));
global.document = {querySelector: selector => elements[selector] || element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: [], stage: "idle"})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const candidate = card({vendor: "switchbot", model: "presence_sensor", sensor_type: "motion", rssi: -42, identifier_suffix: "7fc8", received_at: "now", values: {motion_state: 1, battery_percent: 100, light_level: 12}}, true);
const unknownCandidate = card({vendor: "switchbot", model: "unknown_switchbot", sensor_type: "unknown", rssi: -60, identifier_suffix: "abcd", received_at: "later", values: {}}, true);
const registeredMarkup = registeredCard({device_key: "switchbot:b0e9fee87fc8", sensor_id: "motion-002", display_name: "玄関", location: "玄関", vendor: "switchbot", model: "presence_sensor", sensor_type: "motion", online: true, status: "normal", latest: {received_at: "now", rssi: -42, values: {motion_state: 0, battery_percent: 87, light_level: 7}}});
const missingMarkup = registeredCard({device_key: "switchbot:b0e9fee87fc8", sensor_id: "motion-002", display_name: "玄関", location: "", vendor: "switchbot", model: "presence_sensor", sensor_type: "motion", online: false, status: "offline", latest: {received_at: "now", rssi: -42, values: {motion_state: 0}}});
console.log(JSON.stringify({
  candidate: ["SwitchBot Presence Sensor Pro", "RSSI -42 dBm", "検出", "バッテリー: <strong>100%</strong>", "照度レベル: <strong>12</strong>", "このセンサを登録"].every(value => candidate.includes(value)),
  unknown: ["未対応のSwitchBot機器", "この機器は現在OMKで対応していないため登録できません。", "RSSI -60 dBm", "ID …ABCD", "later"].every(value => unknownCandidate.includes(value)) && !unknownCandidate.includes("このセンサを登録") && !unknownCandidate.includes("値の仕様を確認中"),
  registered: ["motion-002", "玄関 · SwitchBot Presence Sensor Pro", "未検出", "バッテリー</span><strong>87</strong><small>%", "照度レベル</span><strong>7</strong>", "正常"].every(value => registeredMarkup.includes(value)),
  missing: missingMarkup.includes("未検出") && !missingMarkup.includes("undefined") && !missingMarkup.includes("NaN"),
}));
'''
    completed = subprocess.run(["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {"candidate": True, "unknown": True, "registered": True, "missing": True}


def test_broute_status_proxy_returns_only_safe_status(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_identifier = "A" * 32
    raw_password = "B" * 12

    async def successful_request(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("GET", "/api/broute/credentials/status", None)
        return {
            "configured": True,
            "id_masked": "AAAA************************AAAA",
            "password_configured": True,
            "service_active": True,
        }

    monkeypatch.setattr(dashboard_main, "_system_manager_request", successful_request)
    response = client.get("/api/admin/broute-credentials")

    assert response.status_code == 200
    assert response.json()["id_masked"] == "AAAA************************AAAA"
    assert raw_identifier not in response.text
    assert raw_password not in response.text


def test_broute_update_proxy_forwards_body_and_preserves_safe_failure_state(monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = "A" * 32
    password = "B" * 12

    async def restart_failure(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path) == ("PUT", "/api/broute/credentials")
        assert body == {"id": identifier, "password": password}
        raise dashboard_main.HTTPException(
            502,
            {"code": "credentials_saved_restart_failed", "credentials_saved": True},
        )

    monkeypatch.setattr(dashboard_main, "_system_manager_request", restart_failure)
    response = client.put(
        "/api/admin/broute-credentials",
        json={"id": identifier, "password": password},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "credentials_saved_restart_failed",
        "credentials_saved": True,
    }
    assert identifier not in response.text
    assert password not in response.text


def test_broute_retry_proxy_uses_fixed_upstream_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def accepted(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("POST", "/api/broute/retry", None)
        return {"accepted": True}

    monkeypatch.setattr(dashboard_main, "_system_manager_request", accepted)
    response = client.post("/api/admin/broute-retry")

    assert response.json() == {"accepted": True}


@pytest.mark.parametrize(
    ("dashboard_path", "system_manager_path"),
    [
        ("/api/admin/system/reboot", "/api/system/reboot"),
        ("/api/admin/system/shutdown", "/api/system/shutdown"),
    ],
)
def test_system_power_proxy_uses_fixed_upstream_endpoint(
    monkeypatch: pytest.MonkeyPatch, dashboard_path: str, system_manager_path: str
) -> None:
    async def accepted(method: str, path: str, body: dict | None = None) -> dict:
        assert (method, path, body) == ("POST", system_manager_path, None)
        return {"accepted": True}

    monkeypatch.setattr(dashboard_main, "_system_manager_request", accepted)
    response = client.post(dashboard_path)

    assert response.status_code == 200
    assert response.json() == {"accepted": True}


@pytest.mark.parametrize("status_code", [401, 403, 400])
def test_broute_proxy_propagates_system_manager_errors(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    async def failed_request(*_: object, **__: object) -> dict:
        raise dashboard_main.HTTPException(status_code, "upstream error")

    monkeypatch.setattr(dashboard_main, "_system_manager_request", failed_request)
    response = client.get("/api/admin/broute-credentials")

    assert response.status_code == status_code
    assert response.json() == {"detail": "upstream error"}


def test_broute_proxy_reports_missing_system_manager_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", None)

    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert error.value.status_code == 503


def test_broute_proxy_translates_system_manager_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnreachableClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, *_: object, **__: object) -> object:
            raise dashboard_main.httpx.ConnectError("unreachable")

    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", "token-canary")
    monkeypatch.setattr(dashboard_main.httpx, "AsyncClient", lambda **_: UnreachableClient())

    with pytest.raises(dashboard_main.HTTPException) as error:
        asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert error.value.status_code == 503


def test_broute_proxy_adds_bearer_token_only_to_host_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class SuccessfulClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: object):
            captured.update({"method": method, "url": url, **kwargs})
            return type("Response", (), {"status_code": 200, "json": lambda self: {"configured": False}})()

    monkeypatch.setattr(dashboard_main, "SYSTEM_MANAGER_TOKEN", "token-canary")
    monkeypatch.setattr(
        dashboard_main.httpx,
        "AsyncClient",
        lambda **kwargs: captured.update({"client": kwargs}) or SuccessfulClient(),
    )
    response = asyncio.run(dashboard_main._system_manager_request("GET", "/api/broute/credentials/status"))

    assert response == {"configured": False}
    assert captured["url"] == "http://host.docker.internal:8788/api/broute/credentials/status"
    assert captured["headers"] == {"Authorization": "Bearer token-canary"}
    assert captured["client"] == {"timeout": 90}


def test_broute_admin_page_keeps_credentials_and_token_out_of_html() -> None:
    token = "dashboard-token-canary"
    raw_identifier = "A" * 32
    response = client.get("/admin/broute")
    javascript = (Path(__file__).parents[1] / "app" / "static" / "broute.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "Bルート設定" in response.text
    assert 'id="broute-password"' in response.text
    assert 'autocomplete="new-password"' in response.text
    assert 'id="broute-id" name="id" required maxlength="39"' in response.text
    assert 'id="broute-password" name="password" type="password" required maxlength="14"' in response.text
    assert '<label for="broute-id">BルートID</label>' in response.text
    assert '<label for="broute-password">パスワード</label>' in response.text
    assert "接続状態" in response.text
    assert 'id="broute-keyboard-overlay"' in response.text
    assert 'id="broute-keyboard-value"' in response.text
    assert 'id="broute-keyboard-cancel"' in response.text
    assert 'id="broute-keyboard-confirm"' in response.text
    assert token not in response.text + javascript
    assert raw_identifier not in response.text + javascript
    assert "validToken(id, 32)" in javascript
    assert "validToken(pass, 12)" in javascript
    assert "設定を保存しました。Bルートへの接続を開始します。" in javascript
    assert "設定は保存されましたが、Bルートサービスの再起動に失敗しました。" in javascript


def test_access_point_menu_and_page_keep_password_masked_until_revealed() -> None:
    menu = client.get("/admin")
    page = client.get("/admin/access-point")

    assert menu.status_code == 200
    assert 'href="/admin/access-point"' in menu.text
    assert "OMKアクセスポイント参照" in menu.text
    assert page.status_code == 200
    assert "インターネット接続は提供しません" in page.text
    assert 'id="ap-password">••••••••••••' in page.text


def test_access_point_status_and_reveal_generate_escaped_wifi_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def host(method: str, path: str, body: dict | None = None) -> dict:
        calls.append((method, path))
        if path.endswith("/status"):
            return {"ssid": "OMK;,:\\\"", "password_configured": True}
        return {"ssid": "OMK;,:\\\"", "password": "pa;,:\\\"ss"}

    monkeypatch.setattr(dashboard_main, "_system_manager_request", host)
    status = client.get("/api/admin/access-point")
    revealed = client.post("/api/admin/access-point/reveal")

    assert status.json() == {"ssid": "OMK;,:\\\"", "password_configured": True}
    assert revealed.status_code == 200
    assert revealed.json()["password"] == "pa;,:\\\"ss"
    assert "<svg" in revealed.json()["qr_svg"]
    assert calls == [("GET", "/api/access-point/status"), ("GET", "/api/access-point/credentials")]


def test_wifi_qr_escape_follows_wifi_qr_escaping_rules() -> None:
    assert dashboard_main._wifi_qr_escape('a;,:\\"b') == 'a\\;\\,\\:\\\\\\"b'


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for system controls tests")
def test_system_controls_require_confirmation_before_fixed_power_requests() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "system.js"
    response = client.get("/admin/system")
    assert response.status_code == 200
    assert "再起動" in response.text
    assert "シャットダウン" in response.text
    assert 'id="system-reboot"' in response.text
    assert 'id="system-shutdown"' in response.text
    assert 'class="system-confirm-title"' in response.text
    assert 'class="dialog-device system-confirm-message"' in response.text

    harness = r'''
const fs = require("fs"), vm = require("vm");
const elements = {};
function element() { return { textContent: "", disabled: false, listeners: {}, open: false, addEventListener(type, listener) { this.listeners[type] = listener; }, showModal() { this.open = true; }, close() { this.open = false; } }; }
for (const selector of ["#system-reboot", "#system-shutdown", "#system-message", "#system-confirm", "#system-confirm-message", "#system-confirm-cancel", "#system-confirm-execute"]) elements[selector] = element();
const requests = []; let return503 = false;
global.document = {querySelector: (selector) => elements[selector]};
global.window = {setTimeout() {}, location: {assign() {}}};
global.fetch = async (url, options) => { requests.push({url, options}); return {ok: !return503, status: return503 ? 503 : 200, json: async () => return503 ? {detail: "システム管理サービスに接続できません"} : {accepted: true}}; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  elements["#system-reboot"].listeners.click();
  const rebootConfirmation = elements["#system-confirm"].open && elements["#system-confirm-message"].textContent === "OMKを再起動しますか？";
  elements["#system-confirm-cancel"].listeners.click();
  const cancelDoesNotRequest = requests.length === 0 && !elements["#system-confirm"].open;
  elements["#system-shutdown"].listeners.click();
  const shutdownConfirmation = elements["#system-confirm-message"].textContent.includes("電源を入れ直すまで利用できません");
  await elements["#system-confirm-execute"].listeners.click();
  const shutdownRequested = requests.length === 1 && requests[0].url === "/api/admin/system/shutdown" && requests[0].options.method === "POST" && elements["#system-message"].textContent === "OMKをシャットダウンしています…";
  elements["#system-reboot"].listeners.click(); return503 = true;
  await elements["#system-confirm-execute"].listeners.click();
  const interruptedRebootIsNotError = elements["#system-message"].textContent === "OMKを再起動しています…";
  console.log(JSON.stringify({rebootConfirmation, cancelDoesNotRequest, shutdownConfirmation, shutdownRequested, interruptedRebootIsNotError}));
})();
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(javascript_path)], check=True, capture_output=True, text=True
    )
    assert json.loads(completed.stdout) == {
        "rebootConfirmation": True,
        "cancelDoesNotRequest": True,
        "shutdownConfirmation": True,
        "shutdownRequested": True,
        "interruptedRebootIsNotError": True,
    }


def test_admin_menu_has_only_available_management_functions() -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert "<title>OMK 管理メニュー</title>" in response.text
    assert "<h1>管理メニュー</h1>" in response.text
    assert "Bルート設定" in response.text
    assert "システム操作" in response.text
    assert "ネットワーク" not in response.text
    assert "システム情報" not in response.text
    assert "準備中" not in response.text


def test_management_subpages_link_back_to_admin_menu() -> None:
    for path in ("/admin/sensors", "/admin/broute", "/admin/system"):
        response = client.get(path)

        assert response.status_code == 200
        assert "← 管理メニュー" in response.text


def test_broute_layout_uses_wide_grid_rows_with_narrow_screen_fallback() -> None:
    stylesheet = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text(encoding="utf-8")

    assert ".broute-field { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr);" in stylesheet
    assert ".broute-status-list div { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr);" in stylesheet
    assert "@media (max-width: 700px)" in stylesheet
    assert ".broute-status-list div, .broute-field { grid-template-columns: 1fr;" in stylesheet
    assert ".broute-keyboard-overlay { position: fixed;" in stylesheet
    assert ".broute-keyboard-actions { display: grid; grid-template-columns: 1fr 1fr;" in stylesheet


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser formatter tests")
def test_broute_browser_formatter_groups_normalizes_and_submits_unformatted_values() -> None:
    javascript_path = Path(__file__).parents[1] / "app" / "static" / "broute.js"
    keyboard_path = Path(__file__).parents[1] / "app" / "static" / "software_keyboard.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
const elements = {}, documentListeners = {};
function element() { return { value: "", selectionStart: 0, disabled: false, className: "", textContent: "", hidden: false, children: [], listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; }, append(child) { this.children.push(child); }, setAttribute() {}, setSelectionRange(position) { this.selectionStart = position; }, reset() { elements["#broute-id"].value = ""; elements["#broute-password"].value = ""; } }; }
for (const selector of ["#broute-form", "#broute-id", "#broute-password", "#broute-save", "#broute-retry", "#broute-message", "#broute-connection", "#broute-id-masked", "#broute-password-configured", "#broute-keyboard-overlay", "#broute-keyboard-title", "#broute-keyboard-count", "#broute-keyboard-value", "#broute-keyboard-keys", "#broute-keyboard-cancel", "#broute-keyboard-confirm"]) elements[selector] = element();
elements["#broute-keyboard-overlay"].hidden = true;
global.window = globalThis;
global.document = { querySelector: (selector) => elements[selector], createElement: () => element(), addEventListener(type, listener) { documentListeners[type] = listener; } };
const requests = [];
let response = { status: 200, detail: { configured: true, id_masked: "0000************************4CEF", password_configured: true, service_active: true, connection_state: "starting" } }, scheduledDelay = null;
let currentNow = 0;
Date.now = () => currentNow;
global.setTimeout = (_callback, delay) => { scheduledDelay = delay; return 1; };
global.clearTimeout = () => {};
global.fetch = async (url, options = {}) => { requests.push({url, options}); return { status: response.status, ok: response.status < 400, json: async () => response.detail }; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8") + "\nglobalThis.__brouteTest = {formatToken, unformatToken, validToken, appendKeyboardKey, backspaceKeyboardKey, showStatus, stateMessage, pollStatus, loadStatus};");
(async () => {
  const id = "123456789ABCDEF0123456789ABCDEF0", password = "123456789ABC";
  const identifier = elements["#broute-id"], passInput = elements["#broute-password"], overlay = elements["#broute-keyboard-overlay"];
  const passwordBlankOnLoad = passInput.value === "";
  identifier.value = "1234 5678";
  identifier.listeners.pointerdown({preventDefault() {}});
  const idOpened = !overlay.hidden && elements["#broute-keyboard-title"].textContent === "BルートID";
  const assert = require("node:assert/strict");
  const rows = elements["#broute-keyboard-keys"].children;
  assert.deepEqual(rows.map(row => row.children.map(button => button.textContent)), [
    [..."1234567890"], [..."QWERTYUIOP"], [..."ASDFGHJKL"], [..."ZXCVBNM", "⌫", "全消去"]
  ]);
  rows[3].children.find(button => button.textContent === "全消去").listeners.click();
  assert.equal(identifier.value, "");

  __brouteTest.appendKeyboardKey("A");
  __brouteTest.backspaceKeyboardKey();
  elements["#broute-keyboard-cancel"].listeners.click();
  const cancellationRestored = overlay.hidden && identifier.value === "1234 5678";
  function physicalKey(key) { let prevented = false; documentListeners.keydown({key, preventDefault() { prevented = true; }}); return prevented; }
  identifier.value = "";
  identifier.listeners.pointerdown({preventDefault() {}});
  physicalKey("a");
  __brouteTest.appendKeyboardKey("B");
  physicalKey("3");
  const mixedKeyboardInput = identifier.value === "aB3" && elements["#broute-keyboard-value"].textContent === "aB3";
  for (let index = 0; index < 40; index += 1) physicalKey("z");
  const physicalIdMaximum = __brouteTest.unformatToken(identifier.value, 32).length === 32;
  physicalKey("Backspace");
  const physicalBackspace = __brouteTest.unformatToken(identifier.value, 32).length === 31;
  physicalKey("Escape");
  const physicalEscapeRestored = overlay.hidden && identifier.value === "";
  identifier.value = "";
  identifier.listeners.pointerdown({preventDefault() {}});
  for (let index = 0; index < 40; index += 1) __brouteTest.appendKeyboardKey("A");
  const idMaximum = __brouteTest.unformatToken(identifier.value, 32).length === 32 && identifier.value === "AAAA AAAA AAAA AAAA AAAA AAAA AAAA AAAA";
  __brouteTest.backspaceKeyboardKey();
  const idBackspace = __brouteTest.unformatToken(identifier.value, 32).length === 31;
  elements["#broute-keyboard-cancel"].listeners.click();
  passInput.value = "1234";
  passInput.listeners.pointerdown({preventDefault() {}});
  const passwordOpened = !overlay.hidden && elements["#broute-keyboard-title"].textContent === "パスワード";
  for (let index = 0; index < 12; index += 1) __brouteTest.appendKeyboardKey("B");
  const passwordMaximum = __brouteTest.unformatToken(passInput.value, 12).length === 12 && elements["#broute-keyboard-value"].textContent === "1234 BBBB BBBB";
  elements["#broute-keyboard-confirm"].listeners.click();
  const confirmed = overlay.hidden && passInput.value === "1234 BBBB BBBB";
  passInput.value = "ab";
  passInput.listeners.pointerdown({preventDefault() {}});
  physicalKey("c");
  const physicalEnter = physicalKey("Enter") && overlay.hidden && passInput.value === "abc";
  const noPutBeforeSave = !requests.some((request) => request.options.method === "PUT");
  const stateLabels = {};
  for (const state of ["starting", "adapter_missing", "adapter_initializing", "scanning", "authenticating", "connected", "retry_wait", "scan_error", "authentication_error", "connection_error", "status_unavailable"]) { __brouteTest.showStatus({service_active: true, connection_state: state}); stateLabels[state] = elements["#broute-connection"].textContent; }
  __brouteTest.showStatus({service_active: true, connection_state: "retry_wait", connection_attempt: 3});
  const retryButtonVisibleAfterRepeatedFailures = !elements["#broute-retry"].hidden;
  __brouteTest.showStatus({service_active: true, connection_state: "retry_wait", connection_attempt: 2});
  const retryButtonHiddenBeforeExtendedRetry = elements["#broute-retry"].hidden;
  __brouteTest.showStatus({service_active: true, connection_state: "adapter_missing", connection_attempt: 3});
  const retryButtonHiddenWhenAdapterMissing = elements["#broute-retry"].hidden;
  const missingStateIsNotStarting = __brouteTest.showStatus({service_active: true}) === "status_unavailable" && elements["#broute-connection"].textContent === "状態確認中";
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "scanning" } };
  await __brouteTest.pollStatus();
  const idlePolling = elements["#broute-connection"].textContent === "スマートメータを探索中" && scheduledDelay === 10000;
  response = { status: 400, detail: {detail: "入力値が不正です"} };
  await __brouteTest.loadStatus();
  const upstreamErrorIsNotTransport = elements["#broute-connection"].textContent === "状態を取得できません";
  response = { status: 503, detail: {detail: "システム管理サービスに接続できません"} };
  await __brouteTest.loadStatus();
  const transportError = elements["#broute-connection"].textContent === "system-manager接続エラー";
  response = { status: 200, detail: { configured: true, id_masked: "0000************************4CEF", password_configured: true, service_active: true, connection_state: "starting" } };
  elements["#broute-id"].value = "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0";
  elements["#broute-password"].value = "1234 5678 9ABC";
  await elements["#broute-form"].onsubmit({preventDefault() {}});
  const reconnectStates = [];
  for (const state of ["stopped", "starting", "scanning", "retry_wait", "authenticating", "connected"]) {
    response = { status: 200, detail: { configured: true, service_active: state !== "stopped", connection_state: state, retry_after_seconds: state === "retry_wait" ? 30 : null } };
    await __brouteTest.pollStatus();
    reconnectStates.push({state, label: elements["#broute-connection"].textContent, delay: scheduledDelay, message: elements["#broute-message"].textContent});
  }
  async function submitReconnect() {
    elements["#broute-id"].value = "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0";
    elements["#broute-password"].value = "1234 5678 9ABC";
    response = { status: 200, detail: { configured: true, service_active: false, connection_state: "stopped" } };
    await elements["#broute-form"].onsubmit({preventDefault() {}});
  }
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "scan_error" } };
  await __brouteTest.pollStatus();
  const scanErrorTerminal = elements["#broute-message"].textContent === "スマートメータを検出できませんでした。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "authentication_error" } };
  await __brouteTest.pollStatus();
  const authenticationErrorTerminal = elements["#broute-message"].textContent === "認証エラー。BルートIDまたはパスワードを確認してください。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "connection_error" } };
  await __brouteTest.pollStatus();
  const connectionErrorTerminal = elements["#broute-message"].textContent === "スマートメータとの通信に失敗しました。" && scheduledDelay === 10000;
  await submitReconnect();
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "adapter_missing", connection_attempt: 3 } };
  await __brouteTest.pollStatus();
  const adapterMissingStatus = elements["#broute-connection"].textContent === "Bルートアダプターが接続されていません。" && elements["#broute-message"].textContent === "Bルートアダプターを接続してください。" && elements["#broute-retry"].hidden && scheduledDelay === 10000;
  await submitReconnect();
  currentNow = 180001;
  response = { status: 200, detail: { configured: true, service_active: true, connection_state: "retry_wait", retry_after_seconds: 300, connection_attempt: 3 } };
  await __brouteTest.pollStatus();
  const longRetryWaitDoesNotTimeout = elements["#broute-connection"].textContent === "再試行待ち" && elements["#broute-message"].textContent === "スマートメータを検出できません。BルートID・パスワード、または通信状態を確認してください。" && !elements["#broute-retry"].hidden && scheduledDelay === 10000;
  const put = requests.find((request) => request.options.method === "PUT");
  console.log(JSON.stringify({ formattedId: __brouteTest.formatToken(id, 32), formattedPassword: __brouteTest.formatToken(password, 12), normalizedPaste: __brouteTest.unformatToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF0", 32), validPassword: __brouteTest.validToken("1234 5678 9ABC", 12), invalidShortId: __brouteTest.validToken("1234 5678 9ABC DEF0 1234 5678 9ABC DEF", 32), masked: __brouteTest.formatToken("0000************************4CEF", 32), idOpened, passwordOpened, idMaximum, passwordMaximum, idBackspace, cancellationRestored, mixedKeyboardInput, physicalIdMaximum, physicalBackspace, physicalEscapeRestored, physicalEnter, confirmed, noPutBeforeSave, passwordBlankOnLoad, stateLabels, retryButtonVisibleAfterRepeatedFailures, retryButtonHiddenBeforeExtendedRetry, retryButtonHiddenWhenAdapterMissing, missingStateIsNotStarting, adapterMissingMessage: __brouteTest.stateMessage("adapter_missing"), adapterInitializingMessage: __brouteTest.stateMessage("adapter_initializing"), scanErrorMessage: __brouteTest.stateMessage("scan_error"), retryWaitMessage: __brouteTest.stateMessage("retry_wait", 30), idlePolling, upstreamErrorIsNotTransport, transportError, reconnectStates, scanErrorTerminal, authenticationErrorTerminal, connectionErrorTerminal, adapterMissingStatus, longRetryWaitDoesNotTimeout, request: JSON.parse(put.options.body) }));
})();
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(keyboard_path), str(javascript_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["formattedId"] == "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0"
    assert result["formattedPassword"] == "1234 5678 9ABC"
    assert result["normalizedPaste"] == "123456789ABCDEF0123456789ABCDEF0"
    assert result["validId"] is True
    assert result["validPassword"] is True
    assert result["invalidShortId"] is False
    assert result["masked"] == "0000 **** **** **** **** **** **** 4CEF"
    assert result["idOpened"] is True
    assert result["passwordOpened"] is True
    assert result["idMaximum"] is True
    assert result["passwordMaximum"] is True
    assert result["idBackspace"] is True
    assert result["cancellationRestored"] is True
    assert result["mixedKeyboardInput"] is True
    assert result["physicalIdMaximum"] is True
    assert result["physicalBackspace"] is True
    assert result["physicalEscapeRestored"] is True
    assert result["physicalEnter"] is True
    assert result["confirmed"] is True
    assert result["noPutBeforeSave"] is True
    assert result["passwordBlankOnLoad"] is True
    assert result["stateLabels"] == {
        "starting": "起動中",
        "adapter_missing": "Bルートアダプターが接続されていません。",
        "adapter_initializing": "Bルートアダプターを初期化しています。",
        "scanning": "スマートメータを探索中",
        "authenticating": "認証中",
        "connected": "接続済み",
        "retry_wait": "再試行待ち",
        "scan_error": "スマートメータ未検出",
        "authentication_error": "認証エラー",
        "connection_error": "通信エラー",
        "status_unavailable": "状態確認中",
    }
    assert result["missingStateIsNotStarting"] is True
    assert result["retryButtonVisibleAfterRepeatedFailures"] is True
    assert result["retryButtonHiddenBeforeExtendedRetry"] is True
    assert result["retryButtonHiddenWhenAdapterMissing"] is True
    assert result["adapterMissingMessage"] == "Bルートアダプターを接続してください。"
    assert result["adapterInitializingMessage"] == "Bルートアダプターを初期化しています。"
    assert result["scanErrorMessage"] == "スマートメータを検出できませんでした。"
    assert result["retryWaitMessage"] == "30秒後にスマートメータを再探索します。"
    assert result["idlePolling"] is True
    assert result["upstreamErrorIsNotTransport"] is True
    assert result["transportError"] is True
    assert result["reconnectStates"] == [
        {"state": "stopped", "label": "サービス停止", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
        {"state": "starting", "label": "起動中", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
        {"state": "scanning", "label": "スマートメータを探索中", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
        {"state": "retry_wait", "label": "再試行待ち", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
        {"state": "authenticating", "label": "認証中", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
        {"state": "connected", "label": "接続済み", "delay": 10000, "message": "Bルートサービスを開始できませんでした。アダプターの接続を確認してください。"},
    ]
    assert result["scanErrorTerminal"] is True
    assert result["authenticationErrorTerminal"] is True
    assert result["connectionErrorTerminal"] is True
    assert result["adapterMissingStatus"] is True
    assert result["longRetryWaitDoesNotTimeout"] is True
    assert result["request"] == {"id": "123456789ABCDEF0123456789ABCDEF0", "password": "123456789ABC"}


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200


def test_usb_setup_proxy_uses_fixed_endpoint_and_returns_202(monkeypatch):
    body = dict(device='/dev/ttyACM0', node_id='000000000001', confirm_atom_s3_lite=True)
    calls = []
    async def request(method, path, payload=None):
        calls.append((method, path, payload))
        return {'accepted': True} if method == 'POST' else {'stage': 'flashing_firmware'}
    monkeypatch.setattr(dashboard_main, '_system_manager_request', request)
    assert client.post('/api/admin/setup/usb-setup', json=body).status_code == 202
    assert client.get('/api/admin/setup/usb-setup/status').json()['stage'] == 'flashing_firmware'
    assert calls == [('POST', '/api/nodes/usb-setup', body), ('GET', '/api/nodes/usb-setup/status', None)]


@pytest.mark.parametrize("size", ["small", "medium", "large"])
@pytest.mark.parametrize("pattern", ["hero", "strip", "compact"])
@pytest.mark.parametrize("item_count", [2, 3])
@pytest.mark.parametrize("demo_enabled", [False, True])
def test_multi_item_cards_stack_only_small_and_preserve_reading_elements(size, pattern, item_count, demo_enabled):
    items = [
        DisplayItem(id=f"reading-{index}", label=label, short_label=label, group="環境", topic="", device_id="sensor",
                    field=field, value_type="number", unit=unit, category="環境", semantic_role=role,
                    selectable=True, last_received_at="", value=value, freshness="normal",
                    source_kind="real" if index == 0 or not demo_enabled else "demo")
        for index, (label, field, role, value, unit) in enumerate([
            ("外気温", "temperature_c", "temperature", "25.1", "℃"),
            ("外気相対湿度", "relative_humidity_percent", "humidity", "50", "%"),
            ("CO₂濃度", "co2_ppm", "co2", "615", "ppm"),
        ][:item_count])
    ]
    block = DisplayBlockView(id="readings", title="環境", group="環境", size=size, layout_pattern=pattern,
                             primary=items[0], secondary=tuple(items[1:]), freshness="normal", last_received_at="")
    dashboard = dashboard_main.BlockDashboard("custom", [block], "--", "", FreshnessStatus.NORMAL)
    request = SimpleNamespace(url_for=lambda _name, **params: params["path"])
    html = dashboard_main.templates.get_template("display.html").render(
        request=request, dashboard=dashboard, demo_enabled=demo_enabled,
    )
    card = re.search(r'<article.*?</article>', html, re.DOTALL).group(0)
    assert re.findall(r'data-item-id="([^"]+)"', card) == [item.id for item in items]
    if size == "small":
        assert "display-card--stacked" in card
        assert 'class="display-card-stacked-items"' in card
        assert card.count('class="display-stacked-item"') == item_count
        assert "display-card-compact-items" not in card
        assert "display-card-strip-items" not in card
        assert "display-card-primary" not in card
    else:
        assert "stacked" not in card
        assert {"hero": "display-card-primary", "strip": "display-card-strip-items",
                "compact": "display-card-compact-items"}[pattern] in card
    for item in items:
        markup = re.search(r'data-item-id="' + item.id + r'">(.*?)</div>', card, re.DOTALL).group(1)
        assert 'data-role="label"' in markup and item.short_label in markup
        assert 'data-role="value"' in markup and item.value in markup
        assert 'data-role="unit"' in markup and item.unit in markup
        if demo_enabled:
            assert f'data-source-kind="{item.source_kind}">{"模擬" if item.source_kind == "demo" else "実測"}</span>' in markup
        else:
            assert 'data-role="source-kind"' not in markup


def test_small_stacked_css_has_one_column_and_separates_labels_from_values():
    css = (Path(__file__).parents[1] / "app" / "static" / "display.css").read_text()
    card = css.split(".display-card--small.display-card--stacked {", 1)[1].split("}", 1)[0]
    rows = css.split(".display-card-stacked-items {", 1)[1].split("}", 1)[0]
    reading = css.split(".display-stacked-item {", 1)[1].split("}", 1)[0]
    assert all("grid-template-columns: minmax(0, 1fr);" in rule for rule in (card, rows, reading))
    assert "grid-auto-rows: minmax(min-content, 1fr);" in rows
    assert "overflow-y: auto;" in rows  # Constrained screens scroll instead of overlapping rows.
    labels = css.split('.display-card--small.display-card--stacked .display-stacked-item [data-role="label"] {', 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap;" in labels
    assert ".display-stacked-reading {" in css
    assert '.display-stacked-reading [data-role="value"] {' in css
    assert '.display-stacked-reading [data-role="unit"] {' in css
