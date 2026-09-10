"""An invalid/conflicting BLE frame must not show a normal status badge."""
import json
import subprocess
from pathlib import Path


def test_unrecognized_switchbot_renders_without_normal_values_or_status():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm");
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", disabled: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}}; }
global.document = {querySelector: () => element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: []})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const rendered = registeredCard({device_key: "switchbot:020000000040", sensor_id: "test-sensor", display_name: "Test", vendor: "switchbot", model: "waterproof_sensor", sensor_type: "environment", online: true, status: "unrecognized", latest: {received_at: "now", rssi: -50, values: {}}});
console.log(JSON.stringify({unrecognized: rendered.includes("機種・データ未確認"), normal: rendered.includes("正常") || rendered.includes("sensor-state--normal"), invalidNumber: rendered.includes("NaN") || rendered.includes("undefined"), measurement: rendered.includes("registered-values")}));
'''
    result = subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == {
        "unrecognized": True, "normal": False, "invalidNumber": False, "measurement": False,
    }


def test_contact_sensor_uses_product_name_in_registered_card_and_edit_details():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map();
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", disabled: false, hidden: true, checked: false, required: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}, focus() {}}; }
global.document = {querySelector: key => { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); }};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: []})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const sensor = {device_key: "switchbot:020000000040", sensor_id: "contact-001", display_name: "玄関", vendor: "switchbot", model: "contact_sensor", sensor_type: "contact", location: "玄関", online: true, status: "normal", latest: {received_at: "now", rssi: -50, values: {contact_state: 1}}};
assert(registeredCard(sensor).includes("SwitchBot 開閉センサー"));
registered.onclick({target: {closest: () => ({dataset: {sensor: encodeURIComponent(JSON.stringify(sensor))}})}});
assert(document.querySelector("#edit-physical-info").textContent.includes("SwitchBot 開閉センサー · contact"));
assert(!document.querySelector("#edit-physical-info").textContent.includes("contact_sensor"));
'''
    subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)


def test_presence_manual_registration_ui_requires_confirmation_and_resets_choice():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map(), calls = [];
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", disabled: false, hidden: true, checked: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}, focus() {}}; }
global.document = {querySelector: key => { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); }};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => ({sensor_id: "motion-001", sensors: [], nodes: [], candidates: []})}; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  const item = {model: "unknown_switchbot", sensor_type: "unknown", device_key: "switchbot:020000000040", rssi: -51, received_at: "now", values: {}, manual_registration_models: ["presence_sensor"], unconfirmed_preview: {model: "presence_sensor", values: {motion_state: 1, battery_percent: 60, light_level: 7}}};
  const rendered = card(item, true);
  assert(rendered.includes("Presence Sensor Pro候補（未確認）"));
  assert(rendered.includes("未確認プレビュー"));
  assert(rendered.includes("Presence Sensor Proとして解釈した参考値"));
  assert(rendered.includes("検出") && rendered.includes("60%") && rendered.includes("照度レベル"));
  assert(rendered.includes("RSSI -51 dBm") && rendered.includes("最終受信: now") && rendered.includes("ID …0040"));
  assert(rendered.includes("Presence Sensor Pro と確認して登録"));
  assert(rendered.includes("機種は自動判定できません"));
  assert(!card({...item, manual_registration_models: []}, true).includes('class="register"'));
  assert(!card(item, false).includes('class="register"'));
  const plainUnknown = card({...item, manual_registration_models: [], unconfirmed_preview: undefined}, true);
  assert(!plainUnknown.includes("未確認プレビュー"));
  await candidates.onclick({target: {closest: () => ({dataset: {key: item.device_key, confirmedModel: "presence_sensor"}})}});
  assert.strictEqual(document.querySelector("#unconfirmed-model-confirmation").hidden, false);
  assert.strictEqual(document.querySelector("#confirm-unconfirmed-model").checked, false);
  assert.strictEqual(document.querySelector("#confirm-unconfirmed-model").required, true);
  assert(calls.some(call => call.url.includes("confirmed_model=presence_sensor")));
  const countPosts = () => calls.filter(call => call.options.method === "POST").length;
  await form.onsubmit({preventDefault() {}});
  assert.strictEqual(countPosts(), 0);
  document.querySelector("#confirm-unconfirmed-model").checked = true;
  await form.onsubmit({preventDefault() {}});
  assert.strictEqual(countPosts(), 1);
  assert.strictEqual(JSON.parse(calls.find(call => call.options.method === "POST").options.body).confirmed_model, "presence_sensor");
  await candidates.onclick({target: {closest: () => ({dataset: {key: "switchbot:020000000042"}})}});
  assert.strictEqual(document.querySelector("#unconfirmed-model-confirmation").hidden, true);
  assert.strictEqual(document.querySelector("#confirm-unconfirmed-model").required, false);
  await form.onsubmit({preventDefault() {}});
  assert(!("confirmed_model" in JSON.parse(calls.filter(call => call.options.method === "POST")[1].options.body)));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)
    template = (script.parents[1] / "templates/admin_sensors.html").read_text()
    assert 'id="unconfirmed-model-confirmation" hidden' in template
    assert 'id="confirm-unconfirmed-model" type="checkbox"' in template


def test_presence_model_choice_is_forwarded_by_suggestion_proxy(monkeypatch):
    import asyncio
    from urllib.parse import parse_qs, urlsplit
    from app import main

    calls = []

    async def fake_request(method, path):
        calls.append((method, path))
        return {"sensor_id": "motion-001"}

    monkeypatch.setattr(main, "_ble_request", fake_request)
    result = asyncio.run(main.suggested_sensor_id("switchbot:020000000040", "presence_sensor"))
    assert result == {"sensor_id": "motion-001"}
    assert parse_qs(urlsplit(calls[0][1]).query) == {
        "device_key": ["switchbot:020000000040"], "confirmed_model": ["presence_sensor"],
    }


def test_meter_manual_registration_ui_uses_product_name_and_preview_values():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", disabled: false, hidden: true, checked: false, required: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}, focus() {}}; }
global.document = {querySelector: () => element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: []})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const item = {model: "unknown_switchbot", sensor_type: "unknown", device_key: "switchbot:020000000040", rssi: -51, received_at: "now", identifier_suffix: "0040", values: {}, manual_registration_models: ["temperature_humidity_sensor"], unconfirmed_preview: {model: "temperature_humidity_sensor", values: {temperature_c: 24.0, relative_humidity_percent: 52}}};
const rendered = card(item, true);
assert(rendered.includes("SwitchBot 温湿度計候補（未確認）"));
assert(rendered.includes("SwitchBot 温湿度計として解釈した参考値"));
assert(rendered.includes("温度: <strong>24℃</strong>") && rendered.includes("相対湿度: <strong>52%</strong>"));
assert(rendered.includes("SwitchBot 温湿度計と確認して登録"));
assert(rendered.includes("RSSI -51 dBm") && rendered.includes("最終受信: now") && rendered.includes("ID …0040"));
'''
    subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)


def test_co2_manual_registration_ui_uses_product_name_and_preview_values():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", disabled: false, hidden: true, checked: false, required: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}, focus() {}}; }
global.document = {querySelector: () => element()};
global.window = {setInterval() {}, confirm() { return false; }};
global.fetch = async () => ({ok: true, json: async () => ({sensors: [], nodes: []})});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const item = {model: "unknown_switchbot", sensor_type: "unknown", device_key: "switchbot:b0e9fe5815cc", rssi: -51, received_at: "now", identifier_suffix: "15CC", values: {}, manual_registration_models: ["co2_sensor"], unconfirmed_preview: {model: "co2_sensor", values: {co2_ppm: 766, temperature_c: 25.5, relative_humidity_percent: 45}}};
const rendered = card(item, true);
assert(rendered.includes("SwitchBot CO₂センサー候補（未確認）"));
assert(rendered.includes("SwitchBot CO₂センサーとして解釈した参考値"));
assert(rendered.includes("CO₂濃度: <strong>766ppm</strong>") && rendered.includes("温度: <strong>25.5℃</strong>") && rendered.includes("相対湿度: <strong>45%</strong>"));
assert(rendered.includes("SwitchBot CO₂センサーと確認して登録"));
assert(rendered.includes("RSSI -51 dBm") && rendered.includes("最終受信: now") && rendered.includes("ID …15CC"));
'''
    subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)


def test_candidate_poll_reloads_uncached_preview_without_resetting_registration_dialog():
    script = Path(__file__).parents[1] / "app/static/admin.js"
    harness = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const elements = new Map(), intervals = [], calls = [];
function element() { return {addEventListener() {}, querySelectorAll() { return []; }, textContent: "", innerHTML: "", className: "", disabled: false, hidden: true, checked: false, required: false, value: "", dataset: {}, showModal() {}, close() {}, reset() {}, focus() {}}; }
global.document = {querySelector: key => { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); }};
global.window = {setInterval(fn, delay) { intervals.push({fn, delay}); return intervals.length; }, clearInterval() {}, confirm() { return false; }};
global.setInterval = global.window.setInterval; global.clearInterval = global.window.clearInterval;
let candidate = {device_key: "switchbot:020000000040", vendor: "switchbot", model: "unknown_switchbot", sensor_type: "unknown", rssi: -50, received_at: "first", identifier_suffix: "0040", values: {}, manual_registration_models: ["presence_sensor"], unconfirmed_preview: {model: "presence_sensor", values: {motion_state: 0, battery_percent: 100, light_level: 3}}};
let other = {device_key: "switchbot:020000000041", vendor: "switchbot", model: "unknown_switchbot", sensor_type: "unknown", rssi: -61, received_at: "other", identifier_suffix: "0041", values: {}};
global.fetch = async (url, options = {}) => { calls.push({url, options}); if (url.endsWith("/setup/candidates")) return {ok: true, json: async () => ({scanning: true, candidates: [candidate, other]})}; return {ok: true, json: async () => ({status: "scanning", sensors: [], nodes: []})}; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  await start.onclick();
  assert(candidates.innerHTML.includes("未検出") && candidates.innerHTML.includes("照度レベル: <strong>3</strong>"));
  assert(candidates.innerHTML.includes("RSSI -50 dBm") && candidates.innerHTML.includes("最終受信: first"));
  assert(candidates.innerHTML.includes("ID …0040") && candidates.innerHTML.includes("ID …0041"));
  assert(calls.find(call => call.url.endsWith("/setup/candidates")).options.cache === "no-store");
  const poll = intervals.find(interval => interval.delay === 5000);
  assert(poll);
  document.querySelector("#confirm-unconfirmed-model").checked = true;
  candidate = {...candidate, rssi: -44, received_at: "updated", unconfirmed_preview: {model: "presence_sensor", values: {motion_state: 1, battery_percent: 60, light_level: 11}}};
  other = {...other, rssi: -61, received_at: "other", identifier_suffix: "0041"};
  await poll.fn();
  assert(candidates.innerHTML.includes("検出") && candidates.innerHTML.includes("60%") && candidates.innerHTML.includes("照度レベル: <strong>11</strong>"));
  assert(candidates.innerHTML.includes("RSSI -44 dBm") && candidates.innerHTML.includes("最終受信: updated"));
  assert(candidates.innerHTML.includes("ID …0040") && candidates.innerHTML.includes("ID …0041"));
  assert.strictEqual(document.querySelector("#confirm-unconfirmed-model").checked, true);
  candidate = {...candidate, unconfirmed_preview: undefined, manual_registration_models: undefined};
  await poll.fn();
  assert(!candidates.innerHTML.includes("未確認プレビュー"));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)
