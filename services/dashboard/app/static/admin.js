const statusLine = document.querySelector("#setup-status"), candidates = document.querySelector("#candidates"), registered = document.querySelector("#registered-sensors"), nodes = document.querySelector("#omk-nodes"), start = document.querySelector("#start-scan"), stop = document.querySelector("#stop-scan"), dialog = document.querySelector("#register-dialog"), form = document.querySelector("#register-form"), registerError = document.querySelector("#register-error"), editDialog = document.querySelector("#edit-dialog"), editForm = document.querySelector("#edit-form"), editError = document.querySelector("#edit-error");
let poll, candidateRefreshInFlight = false, selectedDeviceKey, confirmedModel, editingDeviceKey, pendingNodeRegistration, nodeRegistrationPoll, latestNodes = [], usbCandidatesByNodeId = new Map(), usbCandidatesInitialized = false, usbProvisioningInProgress = false, logicalIdEditor;
const text = (value) => String(value ?? "—");
const NORMAL_NODE_POLL_MS = 10_000, CANDIDATE_POLL_MS = 5_000, REGISTRATION_NODE_POLL_MS = 1_500, REGISTRATION_TIMEOUT_MS = 30_000;
const LOGICAL_ID_KEY_ROWS = [["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "_"], ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"], ["a", "s", "d", "f", "g", "h", "j", "k", "l"], ["z", "x", "c", "v", "b", "n", "m"]];
const logicalKeyboard = window.createSoftwareKeyboard?.({
  overlay: document.querySelector("#logical-id-keyboard-overlay"), title: document.querySelector("#logical-id-keyboard-title"), count: document.querySelector("#logical-id-keyboard-count"), value: document.querySelector("#logical-id-keyboard-value"), keys: document.querySelector("#logical-id-keyboard-keys"), cancel: document.querySelector("#logical-id-keyboard-cancel"), confirm: document.querySelector("#logical-id-keyboard-confirm")
}, {
  keyRows: state => LOGICAL_ID_KEY_ROWS.map(row => row.map(key => /[a-z]/.test(key) && state.uppercase ? key.toUpperCase() : key)),
  extraActions: state => [[state.uppercase ? "小文字" : "大文字", () => { state.uppercase = !state.uppercase; logicalKeyboard.refresh(); }, "logical-id-keyboard-case-toggle"]],
  lastRowClass: "logical-id-keyboard-row--last",
  showClear: false,
  keyAllowed: key => /^[A-Za-z0-9_-]$/.test(key)
});
function openLogicalKeyboard(button) {
  const nodeId = button.dataset.nodeId, original = button.dataset.logicalId || "", changing = button.dataset.logicalId != null;
  if (!logicalKeyboard || usbProvisioningInProgress || !nodeId || pendingNodeRegistration) return;
  logicalIdEditor = {nodeId, original, changing, input: {value: original, disabled: false, setSelectionRange() {}}};
  document.querySelector("#logical-id-keyboard-confirm").textContent = changing ? "変更内容を登録" : "登録する";
  logicalKeyboard.open(logicalIdEditor.input, {
    label: "Logical ID", length: 48, appendAtEnd: true, target: () => logicalIdEditor?.input || null,
    onConfirm: value => { const editor = logicalIdEditor; logicalIdEditor = undefined; if (editor) submitNodeRegistration(editor.nodeId, value.trim(), editor.changing); },
    onCancel: () => { logicalIdEditor = undefined; }
  });
}

// Put the editor inside the active modal: a body-level overlay is inert while
// showModal() is active. Explicit buttons leave normal input/IME use untouched.
const sensorKeyboardOverlay = document.querySelector("#sensor-keyboard-overlay"), sensorKeyboardInput = document.querySelector("#sensor-keyboard-value");
let sensorKeyboardKana = false;
const SENSOR_KANA_ROWS = [Array.from("あいうえおかきくけこ"), Array.from("さしすせそたちつてと"), Array.from("なにぬねのはひふへほ"), Array.from("まみむめもやゆよらり"), Array.from("るれろわをんー ゛゜")];
const sensorKeyboard = window.createSoftwareKeyboard?.({
  overlay: sensorKeyboardOverlay, title: document.querySelector("#sensor-keyboard-title"), count: document.querySelector("#sensor-keyboard-count"), value: sensorKeyboardInput, keys: document.querySelector("#sensor-keyboard-keys"), cancel: document.querySelector("#sensor-keyboard-cancel"), confirm: document.querySelector("#sensor-keyboard-confirm")
}, {
  nativeInput: true,
  keyAllowed: key => key.length === 1,
  keyRows: state => sensorKeyboardKana ? SENSOR_KANA_ROWS : LOGICAL_ID_KEY_ROWS.map(row => row.map(key => state.uppercase ? key.toUpperCase() : key)),
  extraActions: state => [
    ["かな/英数", () => { sensorKeyboardKana = !sensorKeyboardKana; sensorKeyboard.refresh(); }, ""],
    [state.uppercase ? "小文字" : "大文字", () => { state.uppercase = !state.uppercase; sensorKeyboard.refresh(); }, ""],
  ],
});
sensorKeyboardInput.addEventListener("input", () => {
  document.querySelector("#sensor-keyboard-count").textContent = `${sensorKeyboardInput.value.length} / ${sensorKeyboardInput.maxLength}文字`;
});
for (const [id, label] of [["sensor-id", "OMK センサID"], ["display-name", "表示名"], ["location", "設置場所"], ["edit-sensor-id", "OMK センサID"], ["edit-display-name", "表示名"], ["edit-location", "設置場所"]]) {
  const input = document.querySelector(`#${id}`);
  if (!sensorKeyboard || !input) continue;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "sensor-keyboard-trigger";
  button.textContent = `${label}をソフトウェアキーボードで入力`;
  input.parentElement.append(button);
  button.addEventListener("click", () => {
    sensorKeyboardKana = false;
    input.closest("dialog").append(sensorKeyboardOverlay);
    sensorKeyboardInput.value = input.value;
    sensorKeyboardInput.maxLength = input.maxLength > 0 ? input.maxLength : 64;
    sensorKeyboard.open(sensorKeyboardInput, {
      label, length: sensorKeyboardInput.maxLength, appendAtEnd: true,
      onConfirm: value => { input.value = value; input.dispatchEvent(new Event("input", {bubbles: true})); input.focus(); },
      onCancel: () => input.focus(),
    });
    sensorKeyboardInput.focus();
  });
}
for (const modal of [dialog, editDialog]) {
  modal.addEventListener("close", () => sensorKeyboard?.close(true));
  modal.addEventListener("cancel", event => {
    if (sensorKeyboard && !sensorKeyboardOverlay.hidden) {
      event.preventDefault(); sensorKeyboard.close(true);
    }
  });
}

function formatApiError(value) {
  if (typeof value === "string" && value) return value;
  if (Array.isArray(value)) return value.map(formatApiError).filter(Boolean).join("; ") || "通信エラー";
  if (value && typeof value === "object") {
    if (typeof value.msg === "string") {
      const location = Array.isArray(value.loc) ? value.loc.filter(part => !["body", "query", "path"].includes(part)).join(".") : "";
      return location ? `${location}: ${value.msg}` : value.msg;
    }
    if (typeof value.detail === "string") return value.detail;
  }
  return "通信エラー";
}

function modelName(model) { return model === "temperature_humidity_sensor" ? "温湿度計" : model === "co2_sensor" ? "CO2センサー" : model === "waterproof_sensor" ? "防水温湿度計" : model === "plug_sensor" ? "プラグミニ" : model === "presence_sensor" ? "Presence Sensor Pro" : model === "motion_sensor" ? "人感センサー" : model === "contact_sensor" ? "開閉センサー" : text(model); }
function vendorName(vendor) { return vendor === "switchbot" ? "SwitchBot" : text(vendor); }
function unconfirmedModelName(model) { return model === "temperature_humidity_sensor" ? "SwitchBot 温湿度計" : model === "co2_sensor" ? "SwitchBot CO₂センサー" : model === "waterproof_sensor" ? "SwitchBot 防水温湿度計" : model === "motion_sensor" ? "SwitchBot 人感センサー" : `SwitchBot ${modelName(model)}`; }
function unconfirmedRegistrationName(model) { return ["presence_sensor"].includes(model) ? `${unconfirmedModelName(model)} ` : unconfirmedModelName(model); }
function registeredCard(sensor) { const latest = sensor.latest, values = latest?.values || {}; const readings = sensor.model === "temperature_humidity_sensor" || sensor.model === "waterproof_sensor" ? [["temperature_c", "温度", "℃"], ["relative_humidity_percent", "湿度", "%"]] : sensor.model === "co2_sensor" ? [["temperature_c", "温度", "℃"], ["relative_humidity_percent", "湿度", "%"], ["co2_ppm", "CO₂濃度", "ppm"]] : sensor.model === "plug_sensor" ? [["power_w", "消費電力", "W"]] : sensor.model === "presence_sensor" ? [["battery_percent", "バッテリー", "%"], ["light_level", "照度レベル", ""]] : []; const motion = (sensor.model === "motion_sensor" || sensor.model === "presence_sensor") && values.motion_state != null ? `<p class="motion-reading motion-reading--${values.motion_state === 1 ? "detected" : "clear"}">${sensor.model === "presence_sensor" ? (values.motion_state === 1 ? "検出" : "未検出") : (values.motion_state === 1 ? "検知" : "不在")}</p>` : ""; const contact = sensor.model === "contact_sensor" && values.contact_state != null ? `<p class="motion-reading motion-reading--${values.contact_state === 1 ? "detected" : "clear"}">${values.contact_state === 1 ? "開" : "閉"}</p>` : ""; const plug = sensor.model === "plug_sensor" && values.switch_state != null ? `<p class="motion-reading motion-reading--${values.switch_state === 1 ? "detected" : "clear"}">状態 ${values.switch_state === 1 ? "ON" : "OFF"}</p>` : ""; const readingItems = readings.map(([key, label, unit]) => values[key] == null ? "" : `<p><span>${label}</span><strong>${text(values[key])}</strong><small>${unit}</small></p>`).join(""); const readingsMarkup = readingItems ? `<div class="registered-values">${readingItems}</div>` : ""; const valuesMarkup = !latest ? "<p class=\"sensor-empty\">未受信</p>" : motion || contact ? `${motion || contact}${readingsMarkup}` : readingsMarkup || "<p class=\"sensor-empty\">未対応機種</p>"; const reception = !latest ? "最終受信: 未受信" : `最終受信: ${text(latest.received_at)} · RSSI ${text(latest.rssi)} dBm`; const unrecognized = sensor.status === "unrecognized"; const state = unrecognized ? "機種・データ未確認" : sensor.online ? "正常" : sensor.status === "unreceived" ? "未受信" : "offline"; const data = encodeURIComponent(JSON.stringify(sensor)); return `<article class="sensor-card registered-card"><p class="sensor-id">${text(sensor.sensor_id)}</p><h2>${text(sensor.display_name)}</h2><p>${text(sensor.location || "場所未設定")} · ${vendorName(sensor.vendor)} ${modelName(sensor.model)}</p>${valuesMarkup}${plug}<p class="sensor-reception">${reception}</p><span class="sensor-state sensor-state--${sensor.online && !unrecognized ? "normal" : "offline"}">${state}</span><button class="edit-sensor" data-sensor="${data}">編集</button></article>`; }
function card(item, setup = false) {
  if (item.model === "omk_node") { const values = item.values || {}; return `<article class="sensor-card"><h2>OMK Node</h2><p>RSSI ${text(item.rssi)} dBm</p><p>Node ID: ${text(values.node_id)}</p><p>Protocol: v${text(values.protocol_version)} · Capabilities: ${text((values.capabilities || []).join(", ") || "なし")}</p><p>登録は上のOMK Node一覧から行えます。</p></article>`; }
  const unsupported = item.model === "unknown_switchbot" || item.sensor_type === "unknown", labels = {temperature_c: "温度", relative_humidity_percent: "湿度", co2_ppm: "CO₂濃度", power_w: "消費電力", motion_state: "状態", contact_state: "状態", switch_state: "状態", battery_percent: "バッテリー", light_level: "照度レベル"}, units = {temperature_c: "℃", relative_humidity_percent: "%", co2_ppm: "ppm", power_w: "W", battery_percent: "%"};
  const values = Object.entries(item.values || {}).map(([key, value]) => `<li>${labels[key] || key.replaceAll("_", " ")}: <strong>${key === "motion_state" ? (item.model === "presence_sensor" ? (value === 1 ? "検出" : "未検出") : (value === 1 ? "検知" : "不在")) : key === "contact_state" ? (value === 1 ? "開" : "閉") : key === "switch_state" ? (value === 1 ? "ON" : "OFF") : `${text(value)}${units[key] || ""}`}</strong></li>`).join("");
  const changed = item.highlight === "value_changed" ? `<span class="value-changed">値が変化しました</span>` : "", preview = item.unconfirmed_preview, model = preview?.model, manualModel = unsupported && item.manual_registration_models?.length === 1 ? item.manual_registration_models[0] : null;
  const previewReadings = preview ? [["motion_state", "検知状態", ""], ["battery_percent", "バッテリー", "%"], ["light_level", "照度レベル", ""], ["co2_ppm", "CO₂濃度", "ppm"], ["temperature_c", "温度", "℃"], ["relative_humidity_percent", "相対湿度", "%"]].filter(([key]) => preview.values[key] != null).map(([key, label, unit]) => `<li>${label}: <strong>${key === "motion_state" ? (preview.values[key] === 1 ? "検出" : "未検出") : `${text(preview.values[key])}${unit}`}</strong></li>`).join("") : "";
  const previewMarkup = preview && manualModel === model ? `<div class="candidate-preview"><p><strong>未確認プレビュー</strong></p><p>${unconfirmedModelName(model)}として解釈した参考値です。実機を操作し、表示値が連動することを確認してから登録してください。</p><ul>${previewReadings}</ul></div>` : "";
  const action = setup && manualModel ? `<button data-key="${item.device_key}" data-confirmed-model="${manualModel}" class="register">${unconfirmedRegistrationName(manualModel)}と確認して登録</button>` : setup && !unsupported ? `<button data-key="${item.device_key}" class="register">このセンサを登録</button>` : "";
  const title = manualModel ? `${unconfirmedModelName(manualModel)}候補（未確認）` : unsupported ? "未対応のSwitchBot機器" : `${item.vendor === "switchbot" ? "SwitchBot" : "BLE"} ${modelName(item.model)}`;
  const candidateValues = manualModel ? `<li>機種は自動判定できません。実物が${unconfirmedModelName(manualModel)}であることを確認した場合のみ、機種を指定して登録できます。</li>` : unsupported ? "<li>この機器は現在OMKで対応していないため登録できません。</li>" : values || "<li>値を受信していません</li>";
  return `<article class="sensor-card${item.highlight === "value_changed" ? " is-highlighted" : ""}"><h2>${title}</h2>${changed}<p>${text(item.sensor_type)} · RSSI ${text(item.rssi)} dBm · ID …${text(item.identifier_suffix || item.device_key?.slice(-4)).toUpperCase()}</p>${previewMarkup}<ul>${candidateValues}</ul><p>最終受信: ${text(item.received_at || "未受信")}</p>${action}</article>`;
}

async function api(path, options = {}) { const response = await fetch(`/api/admin${path}`, {headers: {"Content-Type": "application/json"}, ...options}); let data; try { data = await response.json(); } catch (_) { data = null; } if (!response.ok) throw Error(formatApiError(data?.detail ?? data)); return data; }
async function loadRegistered() { try { const data = await api("/sensors"); registered.innerHTML = `<h2>登録済みセンサ</h2>${data.sensors.length ? data.sensors.map(registeredCard).join("") : "<p>登録済みセンサはありません。</p>"}`; } catch (error) { registered.innerHTML = `<p class="error">${error.message}</p>`; } }
function nodeCard(node) { const caps = (node.capabilities || []).map(cap => cap === "ble_scan" ? "BLE relay対応" : cap === "sen66" ? "SEN66対応" : cap).join(" · ") || "なし"; const usbCandidate = usbCandidatesByNodeId.get(node.node_id); const unconfirmedUsbCandidate = usbCandidate?.kind === "unconfirmed_esp32s3"; const usbStatePending = !usbCandidatesInitialized && !node.online; const capabilities = unconfirmedUsbCandidate ? "セットアップ後に確認" : caps; const state = usbCandidate ? (usbCandidate.wifi_configured ? "Wi-Fi設定済み" : "未設定") : usbStatePending ? "確認中" : node.registration_state === "registered" ? "登録済み" : node.registration_state === "provisioned" ? "Wi-Fi設定済み" : "未設定"; const pending = pendingNodeRegistration?.nodeId === node.node_id || node.request_state === "request_sent"; const attachedSensors = unconfirmedUsbCandidate || usbStatePending ? [] : (node.attached_sensors?.length ? node.attached_sensors : (node.connected_sensors || [])); const hasAttachedSensor = attachedSensors.length > 0; const logicalControlsAvailable = !unconfirmedUsbCandidate && !usbStatePending && !usbProvisioningInProgress; const body = !logicalControlsAvailable ? "" : node.registration_state === "provisioned" ? !hasAttachedSensor ? "<p>SEN66を接続している場合は自動検出を待っています。検出後にLogical ID登録操作が表示されます。SEN66を使用しないNodeではLogical ID登録は不要です。</p>" : pending ? "<p>Logical IDの登録要求を送信しました。Nodeの状態更新を待っています。</p>" : `<div class="logical-id-summary"><p class="logical-id-label">Logical ID: 未登録</p></div><button class="edit-node-logical-id register-node" data-node-id="${text(node.node_id)}">Logical IDを登録</button>` : node.registration_state === "registered" ? `<div class="logical-id-summary"><p class="logical-id-label">Logical ID</p><p class="logical-id-value">${text(node.logical_id)}</p></div><button class="edit-node-logical-id register-node" data-node-id="${text(node.node_id)}" data-logical-id="${text(node.logical_id)}">Logical IDを変更</button><button class="remove-node-registration" data-node-id="${text(node.node_id)}" data-logical-id="${text(node.logical_id)}">Logical ID登録を解除</button>` : ""; const sensors = unconfirmedUsbCandidate ? "セットアップ後に確認" : usbStatePending ? "確認中" : hasAttachedSensor ? `SEN66（${node.online ? "検出済み" : "検出情報あり"}）` : "未検出"; return `<article class="sensor-card"><h2>OMK Node</h2><p>Node ID: ${text(node.node_id)}</p><p>接続: ${usbCandidate ? "USB接続" : node.online ? "オンライン" : "最終状態のみ"} · Wi-Fi: ${state}</p><p>対応機能: ${capabilities}</p><p>接続センサ: ${sensors}</p><p>BLE relay: ${node.relay_active ? "稼働中" : "未確認"}</p><p>最終検出: ${text(node.last_seen || node.mqtt_status_seen_at)}</p><span class="sensor-state sensor-state--${node.online ? "normal" : "offline"}">${state}</span>${usbSetupControls(usbCandidate)}${body}</article>`; }
function usbSetupControls(candidate) {
    if (!candidate) return "";
    const unconfirmed = candidate.kind === "unconfirmed_esp32s3";
    const deviceAttribute = String(candidate.device).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    const blocked = candidate.kind === "recovery_required" || (!unconfirmed && candidate.credential_state && candidate.credential_state !== "present");
    const resetLabel = candidate.credential_state === "reinitialize_pending" ? "OMK Nodeの再セットアップを再試行" : "OMK Nodeを再セットアップ";
    const reset = `<button class="provision-usb-node" data-device="${deviceAttribute}" data-node-id="${text(candidate.node_id)}" data-reinitialize="true" ${usbProvisioningInProgress ? "disabled" : ""}>${usbProvisioningInProgress ? "セットアップ中…" : resetLabel}</button>`;
    if (blocked) return `<p>既存または途中状態のNodeです。通常セットアップに必要な管理情報がない、または未完了のため、明示的な再セットアップで設定をやり直せます。</p>${reset}`;
    // Resume an interrupted initial setup without replacing its saved credential.
    const resumeSetup = candidate.kind === "omk_node" && candidate.credential_state === "present" && candidate.wifi_configured === false;
    if (!unconfirmed && !resumeSetup) return reset;
    return `${unconfirmed ? `<p>USB接続されたNode候補：ESP32-S3を検出 · OMK firmware未確認</p><label><input type="checkbox" class="confirm-atom" data-node-id="${text(candidate.node_id)}" ${usbProvisioningInProgress ? "disabled" : ""}>接続した機器が未セットアップのAtomS3 Liteであることを確認しました</label>` : '<p>Wi-Fi設定が未完了です。保存済みの管理情報を維持してセットアップを続けます。</p>'}<p>Gatewayに配置済みのfirmwareを書き込み、Wi-Fiを設定します。</p><button class="provision-usb-node" data-device="${deviceAttribute}" data-node-id="${text(candidate.node_id)}" data-unconfirmed="${unconfirmed}" ${usbProvisioningInProgress ? "disabled" : ""}>${usbProvisioningInProgress ? "セットアップ中…" : "OMK Nodeをセットアップ"}</button>`;
}
function saveNodeInputState() { const saved = {}; nodes.querySelectorAll(".confirm-atom").forEach(input => { saved[input.dataset.nodeId] = {confirmAtom: input.checked}; }); return saved; }
function restoreNodeInputState(saved) { Object.entries(saved).forEach(([nodeId, state]) => { const confirmation = nodes.querySelector(`.confirm-atom[data-node-id="${nodeId}"]`); if (confirmation && state.confirmAtom === true) confirmation.checked = true; }); }
function validLogicalId(value) { return /^[A-Za-z0-9_-]{1,48}$/.test(value); }
function renderNodes() {
    const savedInputs = saveNodeInputState();
    const visible = [...latestNodes, ...[...usbCandidatesByNodeId.values()].filter(candidate => !latestNodes.some(node => node.node_id === candidate.node_id))];
    nodes.innerHTML = `<h2>OMK Node</h2>${visible.length ? visible.map(nodeCard).join("") : "<p>OMK Nodeは未検出です。</p>"}`;
    restoreNodeInputState(savedInputs);
    logicalKeyboard?.refresh();
}
let nodeRefreshPromise;
function loadNodes() {
    if (nodeRefreshPromise) return nodeRefreshPromise;
    nodeRefreshPromise = (async () => {
        const nodeRequest = api("/nodes");
        const usbRequest = usbProvisioningInProgress ? Promise.resolve(null) : api("/setup/usb-nodes");
        const nodeResult = await nodeRequest.then(value => ({status: "fulfilled", value}), reason => ({status: "rejected", reason}));
        if (nodeResult.status === "rejected") {
            nodes.innerHTML = `<p class="error">${nodeResult.reason.message}</p>`;
        } else {
            latestNodes = nodeResult.value.nodes;
            renderNodes();
        }
        const usbResult = await usbRequest.then(value => ({status: "fulfilled", value}), reason => ({status: "rejected", reason}));
        if (usbResult.status === "fulfilled" && usbResult.value) {
            usbCandidatesByNodeId = new Map(usbResult.value.nodes.map(node => [node.node_id, node]));
            usbCandidatesInitialized = true;
            if (nodeResult.status === "fulfilled") renderNodes();
        } else if (usbResult.status === "rejected") {
            statusLine.className = "setup-status error";
            statusLine.textContent = usbResult.reason.message;
        }
        return nodeResult.status === "fulfilled" ? latestNodes : [];
    })().finally(() => { nodeRefreshPromise = undefined; });
    return nodeRefreshPromise;
}
function stopRegistrationPoll(message) { if (nodeRegistrationPoll) clearInterval(nodeRegistrationPoll); nodeRegistrationPoll = undefined; pendingNodeRegistration = undefined; if (message) statusLine.textContent = message; }
async function pollRegistration() { if (!pendingNodeRegistration) return; const pending = pendingNodeRegistration, listed = await loadNodes(); const node = listed.find(item => item.node_id === pending.nodeId); if (node?.registration_state === "registered" && node.logical_id === pending.logicalId) { stopRegistrationPoll(pending.changing ? "Logical IDを変更しました。" : "Logical IDを登録しました。"); return; } if (Date.now() >= pending.deadline) stopRegistrationPoll("Logical IDの登録要求を送信しました。Nodeの状態更新を待っています。"); }
function beginRegistrationPoll(nodeId, logicalId, changing) { if (nodeRegistrationPoll) clearInterval(nodeRegistrationPoll); pendingNodeRegistration = {nodeId, logicalId, changing, deadline: Date.now() + REGISTRATION_TIMEOUT_MS}; nodeRegistrationPoll = setInterval(pollRegistration, REGISTRATION_NODE_POLL_MS); }
async function refreshCandidates() { if (candidateRefreshInFlight) return; candidateRefreshInFlight = true; try { const data = await api("/setup/candidates", {cache: "no-store"}); candidates.innerHTML = `<h2>未登録デバイス</h2>${data.candidates.length ? data.candidates.map((candidate) => card(candidate, true)).join("") : "<p>未登録のSwitchBotまたはOMK Node advertisementを待っています…</p>"}`; if (!data.scanning) finish("探索時間が終了しました。必要ならもう一度開始してください。"); } catch (error) { finish(error.message, true); } finally { candidateRefreshInFlight = false; } }
function finish(message, error = false) { clearInterval(poll); start.hidden = false; stop.hidden = true; statusLine.textContent = message; statusLine.className = `setup-status${error ? " error" : ""}`; }
start.onclick = async () => { try { await api("/setup/scan", {method: "POST"}); start.hidden = true; stop.hidden = false; candidates.hidden = false; statusLine.textContent = "周囲の未登録SwitchBotセンサを探索中です。"; await refreshCandidates(); clearInterval(poll); poll = setInterval(refreshCandidates, CANDIDATE_POLL_MS); } catch (error) { finish(error.message, true); } };
stop.onclick = async () => { await api("/setup/scan", {method: "DELETE"}); finish("探索を中止しました。"); };
candidates.onclick = async (event) => { const button = event.target.closest(".register"); if (!button) return; selectedDeviceKey = button.dataset.key; confirmedModel = button.dataset.confirmedModel || null; document.querySelector("#register-device").textContent = `対象: ${selectedDeviceKey}`; registerError.hidden = true; form.reset(); const confirmation = document.querySelector("#confirm-unconfirmed-model"); confirmation.checked = false; confirmation.required = Boolean(confirmedModel); document.querySelector("#unconfirmed-model-confirmation").hidden = !confirmedModel; document.querySelector("#confirmed-model-name").textContent = confirmedModel ? unconfirmedModelName(confirmedModel) : ""; try { document.querySelector("#sensor-id").value = (await api(`/setup/suggested-sensor-id?device_key=${encodeURIComponent(selectedDeviceKey)}${confirmedModel ? "&confirmed_model=" + encodeURIComponent(confirmedModel) : ""}`)).sensor_id; } catch (error) { registerError.textContent = error.message; registerError.hidden = false; } dialog.showModal(); document.querySelector("#sensor-id").focus(); };
async function submitNodeRegistration(nodeId, logicalId, changing) { if (pendingNodeRegistration) return; if (!validLogicalId(logicalId)) { statusLine.textContent = "Logical IDは1〜48文字の英数字、-、_で入力してください。"; statusLine.className = "setup-status error"; return; } try { await api(`/nodes/${encodeURIComponent(nodeId)}/register`, {method: "POST", body: JSON.stringify({logical_id: logicalId})}); statusLine.className = "setup-status"; statusLine.textContent = changing ? "Logical IDを変更しています…" : "Logical IDを登録しています…"; beginRegistrationPoll(nodeId, logicalId, changing); await pollRegistration(); } catch (error) { statusLine.textContent = error.message; statusLine.className = "setup-status error"; } }
const usbSetupStages = {reinitializing_node: "Nodeの既存設定を初期化しています", verifying_reinitialize: "新しい管理情報と登録解除を確認しています", idle: "待機中", validating_firmware: "firmware package確認", checking_device: "USB機器確認", flashing_factory: "初回factory設定", flashing_firmware: "firmware書込み", waiting_for_node: "Node再起動／再認識", configuring_wifi: "Wi-Fi設定", waiting_for_registration: "OMK接続確認", completed: "セットアップ完了", failed: "セットアップ失敗"};
const usbSetupErrors = {
    reinitialize_required: "管理情報がない既存Nodeです。「再セットアップ」から設定をやり直してください。",
    reinitialize_confirmation_required: "既存設定を消去する再セットアップの確認が必要です。",
    reinitialize_firmware_required: "配置済みfirmwareが再セットアップに未対応です。対応packageを配置してください。",
    credential_store_invalid: "管理情報が不正または再セットアップが途中です。再セットアップから再試行してください。",
    firmware_package_missing: "Gatewayのfirmware packageがありません。管理者に配置を確認してください。",
    firmware_package_invalid: "Gatewayのfirmware packageを検証できません。管理者に確認してください。",
    node_not_available: "対象のUSB機器が見つかりません。接続を確認してください。",
    device_inspection_failed: "USB機器の確認に失敗しました。接続を確認してください。",
    unsupported_chip: "対応するESP32-S3を確認できませんでした。",
    ambiguous_mac: "機器を一意に識別できません。対象機器の接続を確認してください。",
    node_identity_changed: "対象機器の同一性を確認できないため停止しました。",
    atom_s3_lite_confirmation_required: "未セットアップのAtomS3 Lite実物確認が必要です。",
    recovery_required: "初回セットアップはできません。既存Nodeとして再セットアップを選択してください。",
    factory_write_failed: "初回/再セットアップの管理情報書込みに失敗しました。保存済み情報を残し、再セットアップから再試行できます。",
    firmware_write_failed: "firmware書込みに失敗しました。管理情報を削除せず接続を確認してください。",
    node_reappearance_timeout: "再起動後のNodeを確認できませんでした。接続を確認してください。",
    unsupported_usb_protocol: "NodeのUSB設定protocolを確認できませんでした。",
    gateway_credential_unavailable: "GatewayのWi-Fi設定を取得できませんでした。",
    set_wifi_failed: "NodeへWi-Fi設定を送信できませんでした。",
    storage_error: "Nodeの設定保存または再セットアップの検証に失敗しました。",
    restart_error: "Nodeが再起動を開始できませんでした。",
    invalid_request: "Nodeが設定要求を受け付けませんでした。",
    busy: "Nodeが別の処理を実行中です。",
    serial_busy: "USB機器の使用待ちがタイムアウトしました。",
    mqtt_registration_timeout: "再起動後のOMK接続を確認できませんでした。GatewayとNodeの接続状態を確認してください。",
    setup_failed: "セットアップに失敗しました。管理者に確認してください。"
};
function usbSetupErrorMessage(code) {
    return Object.hasOwn(usbSetupErrors, code) ? usbSetupErrors[code] : usbSetupErrors.setup_failed;
}
let usbSetupPoll;
async function pollUsbSetup(submissionFailed = false, showTerminal = false) {
    try {
        const job = await api("/setup/usb-setup/status");
        usbProvisioningInProgress = !["idle", "completed", "failed"].includes(job.stage);
        if (submissionFailed && !usbProvisioningInProgress) {
            statusLine.className = "setup-status error";
            statusLine.textContent = "今回のセットアップ要求の受付を確認できませんでした。接続を確認し、必要なら再実行してください。";
            await loadNodes();
            return;
        }
        if (job.stage !== "idle" && (!['completed', 'failed'].includes(job.stage) || showTerminal)) {
            statusLine.className = `setup-status${job.stage === "failed" ? " error" : ""}`;
            statusLine.textContent = `${job.node_id}: ${usbSetupStages[job.stage] || "処理中"}${job.error ? ` ${usbSetupErrorMessage(job.error)}` : ""}`;
        }
        if (usbProvisioningInProgress) { renderNodes(); usbSetupPoll = setTimeout(() => pollUsbSetup(submissionFailed, true), 1000); }
        else if (job.stage !== "idle" && showTerminal) { await loadNodes(); }
    } catch (error) {
        // A network error is not proof that the host job ended. Keep polling.
        statusLine.textContent = "setup状態を確認できません。接続回復を待っています。";
        usbSetupPoll = setTimeout(() => pollUsbSetup(submissionFailed, showTerminal), 2000);
    }
}
async function submitUsbProvision(button) {
    if (button.disabled || usbProvisioningInProgress) return;
    const confirmed = button.closest("article").querySelector(".confirm-atom")?.checked === true;
    const reinitialize = button.dataset.reinitialize === "true";
    if (reinitialize && !window.confirm(`OMK Nodeを再セットアップしますか？\nNode ID: ${button.dataset.nodeId}\n\n接続した機器がAtomS3 Liteであることを確認してください。\n既存provisioning credentialを新しいcredentialへ置換します。\nWi-Fi設定を消去し、新Gateway用に再設定します。\nLogical IDを消去するため、Node・接続センサの設定を再度行う必要があります。\n\n途中失敗した場合は同じ「再セットアップ」操作で再試行できます。`)) return;
    if (!reinitialize && button.dataset.unconfirmed === "true" && !confirmed) {
        statusLine.textContent = "未セットアップのAtomS3 Lite実物確認が必要です。";
        return;
    }
    usbProvisioningInProgress = true;
    button.disabled = true;
    button.textContent = "セットアップ中…";
    renderNodes();
    clearTimeout(usbSetupPoll);
    let submissionFailed = false;
    try {
        const body = {device: button.dataset.device, node_id: button.dataset.nodeId};
        if (reinitialize) body.confirm_reinitialize = true;
        else body.confirm_atom_s3_lite = confirmed;
        await api(reinitialize ? "/setup/usb-reinitialize" : "/setup/usb-setup", {method: "POST", body: JSON.stringify(body)});
    } catch (error) { submissionFailed = true; }
    await pollUsbSetup(submissionFailed, true);
}
// Restore progress after navigation/reload, without exposing the host token.
pollUsbSetup();
nodes.onclick = async (event) => { const provision = event.target.closest(".provision-usb-node"); if (provision) { await submitUsbProvision(provision); return; } const edit = event.target.closest(".edit-node-logical-id"); if (edit) { openLogicalKeyboard(edit); return; } const remove = event.target.closest(".remove-node-registration"); if (!remove || !window.confirm(`Logical ID「${remove.dataset.logicalId}」の登録を解除しますか？\n\nWi-Fi設定と過去の計測データは削除されません。\nこのNodeのLogical IDだけが未登録状態になります。`)) return; try { await api(`/nodes/${encodeURIComponent(remove.dataset.nodeId)}/registration`, {method: "DELETE"}); await loadNodes(); statusLine.className = "setup-status"; statusLine.textContent = "Logical IDの登録を解除しました。"; } catch (error) { statusLine.className = "setup-status error"; statusLine.textContent = error.message; } };
registered.onclick = (event) => { const button = event.target.closest(".edit-sensor"); if (!button) return; const sensor = JSON.parse(decodeURIComponent(button.dataset.sensor)); editingDeviceKey = sensor.device_key; editError.hidden = true; document.querySelector("#edit-physical-info").textContent = `${sensor.device_key} · ${vendorName(sensor.vendor)} ${modelName(sensor.model)} · ${sensor.sensor_type}`; document.querySelector("#edit-sensor-id").value = sensor.sensor_id; document.querySelector("#edit-display-name").value = sensor.display_name; document.querySelector("#edit-location").value = sensor.location; document.querySelector("#edit-enabled").checked = sensor.enabled; editDialog.showModal(); };
document.querySelector("#cancel-register").onclick = () => dialog.close(); document.querySelector("#cancel-edit").onclick = () => editDialog.close();
form.onsubmit = async (event) => { event.preventDefault(); if (confirmedModel && !document.querySelector("#confirm-unconfirmed-model").checked) { registerError.textContent = "実物の機種を確認してください"; registerError.hidden = false; return; } try { await api("/sensors", {method: "POST", body: JSON.stringify({device_key: selectedDeviceKey, ...(confirmedModel ? {confirmed_model: confirmedModel} : {}), sensor_id: document.querySelector("#sensor-id").value, display_name: document.querySelector("#display-name").value, location: document.querySelector("#location").value})}); dialog.close(); await Promise.all([refreshCandidates(), loadRegistered()]); statusLine.textContent = "登録しました。ほかの候補の順序はそのままです。"; } catch (error) { registerError.textContent = error.message; registerError.hidden = false; } };
editForm.onsubmit = async (event) => { event.preventDefault(); try { await api(`/sensors/${encodeURIComponent(editingDeviceKey)}`, {method: "PATCH", body: JSON.stringify({sensor_id: document.querySelector("#edit-sensor-id").value, display_name: document.querySelector("#edit-display-name").value, location: document.querySelector("#edit-location").value, enabled: document.querySelector("#edit-enabled").checked})}); editDialog.close(); await loadRegistered(); } catch (error) { editError.textContent = error.message; editError.hidden = false; } };
document.querySelector("#delete-sensor").onclick = async () => { const sensorId = document.querySelector("#edit-sensor-id").value; const displayName = document.querySelector("#edit-display-name").value; if (!editingDeviceKey || !window.confirm(`「${displayName}（${sensorId}）」の登録を削除しますか？\n過去の計測データは削除されません。`)) return; editError.hidden = true; try { await api(`/sensors/${encodeURIComponent(editingDeviceKey)}`, {method: "DELETE"}); editDialog.close(); await loadRegistered(); if (!candidates.hidden) await refreshCandidates(); statusLine.textContent = "センサ登録を削除しました。過去の計測データは保持されています。"; } catch (error) { if (error.message.includes("not found")) { editDialog.close(); await loadRegistered(); if (!candidates.hidden) await refreshCandidates(); statusLine.textContent = "センサ登録はすでに削除されています。"; return; } editError.textContent = `センサ登録を削除できませんでした: ${error.message}`; editError.hidden = false; } };
loadRegistered(); loadNodes(); window.setInterval(() => { loadRegistered(); loadNodes(); }, NORMAL_NODE_POLL_MS);
