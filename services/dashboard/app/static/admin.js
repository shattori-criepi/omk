const menu = document.querySelector("#admin-menu");
const sensors = document.querySelector("#sensor-management");
const statusLine = document.querySelector("#setup-status");
const candidates = document.querySelector("#candidates");
const registered = document.querySelector("#registered-sensors");
const start = document.querySelector("#start-scan");
const stop = document.querySelector("#stop-scan");
let poll;

const text = (value) => String(value ?? "—");
function card(item, setup = false) {
  const values = Object.entries(item.values || {}).map(([key, value]) => `<li>${key.replaceAll("_", " ")}: <strong>${text(value)}</strong></li>`).join("");
  const action = setup && !item.registered ? `<button data-key="${item.device_key}" class="register">このセンサを登録</button>` : `<span>${item.registered ? "登録済み" : ""}</span>`;
  return `<article class="sensor-card"><h2>${item.vendor === "switchbot" ? "SwitchBot" : "BLE"} ${text(item.model)}</h2><p>${text(item.sensor_type)} · RSSI ${text(item.rssi)} dBm · ID …${text(item.identifier_suffix || item.device_key?.slice(-4)).toUpperCase()}</p><ul>${values || "<li>値の仕様を確認中（raw advertisement を保存可能）</li>"}</ul><p>${text(item.received_at || "未受信")}</p>${action}</article>`;
}
async function api(path, options = {}) { const r = await fetch(`/api/admin${path}`, {headers: {"Content-Type": "application/json"}, ...options}); const data = await r.json(); if (!r.ok) throw Error(data.detail || "通信エラー"); return data; }
async function loadRegistered() { try { const data = await api("/sensors"); registered.innerHTML = data.sensors.length ? data.sensors.map((s) => card(s)).join("") : "<p>登録済みセンサはありません。</p>"; } catch (e) { registered.innerHTML = `<p class="error">${e.message}</p>`; } }
async function refreshCandidates() { try { const data = await api("/setup/candidates"); candidates.innerHTML = data.candidates.length ? `<h2>検出候補</h2>${data.candidates.map((c) => card(c, true)).join("")}` : "<p>SwitchBot の advertisement を待っています…</p>"; if (!data.scanning) { finish("探索時間が終了しました。必要ならもう一度開始してください。"); } } catch (e) { finish(e.message, true); } }
function finish(message, error = false) { clearInterval(poll); start.hidden = false; stop.hidden = true; statusLine.textContent = message; statusLine.className = `setup-status${error ? " error" : ""}`; }
document.querySelector("#open-sensors").onclick = () => { menu.hidden = true; sensors.hidden = false; loadRegistered(); };
document.querySelector("#back-menu").onclick = () => { finish("登録済みセンサを表示しています。"); sensors.hidden = true; menu.hidden = false; };
start.onclick = async () => { try { await api("/setup/scan", {method: "POST"}); start.hidden = true; stop.hidden = false; candidates.hidden = false; statusLine.textContent = "周囲の SwitchBot センサを探索中です。温湿度計は手で持つ、開閉・人感センサは状態を変えると見つけやすくなります。"; await refreshCandidates(); poll = setInterval(refreshCandidates, 2000); } catch (e) { finish(e.message, true); } };
stop.onclick = async () => { await api("/setup/scan", {method: "DELETE"}); finish("探索を中止しました。"); };
candidates.onclick = async (event) => { const button = event.target.closest(".register"); if (!button) return; const sensorId = prompt("OMK センサID（例: living-env-01）"); const displayName = prompt("表示名（例: リビング）"); if (!sensorId || !displayName) return; try { await api("/sensors", {method: "POST", body: JSON.stringify({device_key: button.dataset.key, sensor_id: sensorId, display_name: displayName})}); await api("/setup/scan", {method: "DELETE"}); finish("登録しました。通常計測を開始します。"); candidates.hidden = true; loadRegistered(); } catch (e) { alert(e.message); } };
