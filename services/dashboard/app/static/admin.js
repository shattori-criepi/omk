const statusLine = document.querySelector("#setup-status");
const candidates = document.querySelector("#candidates");
const registered = document.querySelector("#registered-sensors");
const start = document.querySelector("#start-scan");
const stop = document.querySelector("#stop-scan");
const dialog = document.querySelector("#register-dialog");
const form = document.querySelector("#register-form");
const registerError = document.querySelector("#register-error");
let poll;
let selectedDeviceKey;

const text = (value) => String(value ?? "—");
function registeredCard(sensor) {
  const latest = sensor.latest;
  const values = latest?.values || {};
  const readings = sensor.model === "meter" || sensor.model === "meter_plus"
    ? [["temperature_c", "温度", "℃"], ["relative_humidity_percent", "湿度", "%"]]
    : sensor.model === "meter_pro_co2"
      ? [["temperature_c", "温度", "℃"], ["relative_humidity_percent", "湿度", "%"], ["co2_ppm", "CO2", "ppm"]]
      : [];
  const valuesMarkup = !latest ? "<p class=\"sensor-empty\">未受信</p>"
    : readings.length === 0 ? "<p class=\"sensor-empty\">未対応機種</p>"
      : `<div class="registered-values">${readings.map(([key, label, unit]) => values[key] == null ? "" : `<p><span>${label}</span><strong>${text(values[key])}</strong><small>${unit}</small></p>`).join("") || "<p class=\"sensor-empty\">対応値なし</p>"}</div>`;
  const reception = !latest ? "最終受信: 未受信" : `最終受信: ${text(latest.received_at)} · RSSI ${text(latest.rssi)} dBm`;
  const state = sensor.online ? "正常" : sensor.status === "unreceived" ? "未受信" : "offline";
  return `<article class="sensor-card registered-card"><p class="sensor-id">${text(sensor.sensor_id)}</p><h2>${text(sensor.display_name)}</h2><p>${text(sensor.location || "場所未設定")} · ${text(sensor.vendor)} ${text(sensor.model)}</p>${valuesMarkup}<p class="sensor-reception">${reception}</p><span class="sensor-state sensor-state--${sensor.online ? "normal" : "offline"}">${state}</span></article>`;
}
function card(item, setup = false) {
  const values = Object.entries(item.values || {}).map(([key, value]) => `<li>${key.replaceAll("_", " ")}: <strong>${text(value)}</strong></li>`).join("");
  const changed = item.highlight === "value_changed" ? `<span class="value-changed">値が変化しました</span>` : "";
  const action = setup ? `<button data-key="${item.device_key}" class="register">このセンサを登録</button>` : `<span class="sensor-state">${text(item.status || "登録済み")}</span>`;
  return `<article class="sensor-card${item.highlight === "value_changed" ? " is-highlighted" : ""}"><h2>${item.vendor === "switchbot" ? "SwitchBot" : "BLE"} ${text(item.model)}</h2>${changed}<p>${text(item.sensor_type)} · RSSI ${text(item.rssi)} dBm · ID …${text(item.identifier_suffix || item.device_key?.slice(-4)).toUpperCase()}</p><ul>${values || "<li>値の仕様を確認中（raw advertisement を保存可能）</li>"}</ul><p>${text(item.received_at || item.last_received_at || "未受信")}</p>${action}</article>`;
}
async function api(path, options = {}) { const response = await fetch(`/api/admin${path}`, {headers: {"Content-Type": "application/json"}, ...options}); const data = await response.json(); if (!response.ok) throw Error(data.detail || "通信エラー"); return data; }
async function loadRegistered() { try { const data = await api("/sensors"); registered.innerHTML = `<h2>登録済みセンサ</h2>${data.sensors.length ? data.sensors.map(registeredCard).join("") : "<p>登録済みセンサはありません。</p>"}`; } catch (error) { registered.innerHTML = `<p class="error">${error.message}</p>`; } }
async function refreshCandidates() { try { const data = await api("/setup/candidates"); candidates.innerHTML = `<h2>未登録センサ</h2>${data.candidates.length ? data.candidates.map((candidate) => card(candidate, true)).join("") : "<p>未登録の SwitchBot advertisement を待っています…</p>"}`; if (!data.scanning) finish("探索時間が終了しました。必要ならもう一度開始してください。"); } catch (error) { finish(error.message, true); } }
function finish(message, error = false) { clearInterval(poll); start.hidden = false; stop.hidden = true; statusLine.textContent = message; statusLine.className = `setup-status${error ? " error" : ""}`; }
start.onclick = async () => { try { await api("/setup/scan", {method: "POST"}); start.hidden = true; stop.hidden = false; candidates.hidden = false; statusLine.textContent = "周囲の未登録 SwitchBot センサを探索中です。候補の順序はこの探索中は固定されます。"; await refreshCandidates(); poll = setInterval(refreshCandidates, 2000); } catch (error) { finish(error.message, true); } };
stop.onclick = async () => { await api("/setup/scan", {method: "DELETE"}); finish("探索を中止しました。"); };
candidates.onclick = async (event) => { const button = event.target.closest(".register"); if (!button) return; selectedDeviceKey = button.dataset.key; document.querySelector("#register-device").textContent = `対象: ${selectedDeviceKey}`; registerError.hidden = true; form.reset(); try { const suggestion = await api(`/setup/suggested-sensor-id?device_key=${encodeURIComponent(selectedDeviceKey)}`); document.querySelector("#sensor-id").value = suggestion.sensor_id; } catch (error) { registerError.textContent = error.message; registerError.hidden = false; } dialog.showModal(); document.querySelector("#sensor-id").focus(); };
document.querySelector("#cancel-register").onclick = () => dialog.close();
form.onsubmit = async (event) => { event.preventDefault(); try { await api("/sensors", {method: "POST", body: JSON.stringify({device_key: selectedDeviceKey, sensor_id: document.querySelector("#sensor-id").value, display_name: document.querySelector("#display-name").value, location: document.querySelector("#location").value})}); dialog.close(); document.querySelector(`[data-key="${selectedDeviceKey}"]`)?.closest(".sensor-card")?.remove(); await Promise.all([refreshCandidates(), loadRegistered()]); statusLine.textContent = "登録しました。ほかの候補の順序はそのままです。"; } catch (error) { registerError.textContent = error.message; registerError.hidden = false; } };
loadRegistered();
window.setInterval(loadRegistered, 10_000);
