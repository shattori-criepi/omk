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
function card(item, setup = false) {
  const values = Object.entries(item.values || {}).map(([key, value]) => `<li>${key.replaceAll("_", " ")}: <strong>${text(value)}</strong></li>`).join("");
  const action = setup && !item.registered ? `<button data-key="${item.device_key}" class="register">このセンサを登録</button>` : `<span>${item.registered ? "登録済み" : ""}</span>`;
  return `<article class="sensor-card"><h2>${item.vendor === "switchbot" ? "SwitchBot" : "BLE"} ${text(item.model)}</h2><p>${text(item.sensor_type)} · RSSI ${text(item.rssi)} dBm · ID …${text(item.identifier_suffix || item.device_key?.slice(-4)).toUpperCase()}</p><ul>${values || "<li>値の仕様を確認中（raw advertisement を保存可能）</li>"}</ul><p>${text(item.received_at || item.last_received_at || "未受信")}</p>${action}</article>`;
}
async function api(path, options = {}) { const response = await fetch(`/api/admin${path}`, {headers: {"Content-Type": "application/json"}, ...options}); const data = await response.json(); if (!response.ok) throw Error(data.detail || "通信エラー"); return data; }
async function loadRegistered() { try { const data = await api("/sensors"); registered.innerHTML = `<h2>登録済みセンサ</h2>${data.sensors.length ? data.sensors.map((sensor) => card(sensor)).join("") : "<p>登録済みセンサはありません。</p>"}`; } catch (error) { registered.innerHTML = `<p class="error">${error.message}</p>`; } }
async function refreshCandidates() { try { const data = await api("/setup/candidates"); candidates.innerHTML = `<h2>検出候補</h2>${data.candidates.length ? data.candidates.map((candidate) => card(candidate, true)).join("") : "<p>SwitchBot の advertisement を待っています…</p>"}`; if (!data.scanning) finish("探索時間が終了しました。必要ならもう一度開始してください。"); } catch (error) { finish(error.message, true); } }
function finish(message, error = false) { clearInterval(poll); start.hidden = false; stop.hidden = true; statusLine.textContent = message; statusLine.className = `setup-status${error ? " error" : ""}`; }
start.onclick = async () => { try { await api("/setup/scan", {method: "POST"}); start.hidden = true; stop.hidden = false; candidates.hidden = false; statusLine.textContent = "周囲の SwitchBot センサを探索中です。対象センサを操作すると見つけやすくなります。"; await refreshCandidates(); poll = setInterval(refreshCandidates, 2000); } catch (error) { finish(error.message, true); } };
stop.onclick = async () => { await api("/setup/scan", {method: "DELETE"}); finish("探索を中止しました。"); };
candidates.onclick = (event) => { const button = event.target.closest(".register"); if (!button) return; selectedDeviceKey = button.dataset.key; document.querySelector("#register-device").textContent = `対象: ${selectedDeviceKey}`; registerError.hidden = true; form.reset(); dialog.showModal(); document.querySelector("#sensor-id").focus(); };
document.querySelector("#cancel-register").onclick = () => dialog.close();
form.onsubmit = async (event) => { event.preventDefault(); try { await api("/sensors", {method: "POST", body: JSON.stringify({device_key: selectedDeviceKey, sensor_id: document.querySelector("#sensor-id").value, display_name: document.querySelector("#display-name").value, location: document.querySelector("#location").value})}); await api("/setup/scan", {method: "DELETE"}); dialog.close(); candidates.hidden = true; finish("登録しました。通常計測を開始します。"); loadRegistered(); } catch (error) { registerError.textContent = error.message; registerError.hidden = false; } };
loadRegistered();
