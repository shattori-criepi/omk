const rebootButton = document.querySelector("#system-reboot"), shutdownButton = document.querySelector("#system-shutdown"), message = document.querySelector("#system-message"), confirmDialog = document.querySelector("#system-confirm"), confirmMessage = document.querySelector("#system-confirm-message"), cancelButton = document.querySelector("#system-confirm-cancel"), executeButton = document.querySelector("#system-confirm-execute");
let requestedAction = null;

const actions = {
  reboot: {path: "/system/reboot", confirm: "OMKを再起動しますか？", progress: "OMKを再起動しています…"},
  shutdown: {path: "/system/shutdown", confirm: "OMKをシャットダウンします。\nシャットダウン後は電源を入れ直すまで利用できません。\n実行しますか？", progress: "OMKをシャットダウンしています…"},
};

async function api(path) { const response = await fetch(`/api/admin${path}`, {method: "POST", headers: {"Content-Type": "application/json"}}); if (!response.ok) { const data = await response.json().catch(() => ({})); const error = new Error(data.detail?.code || data.detail || "システム操作に失敗しました"); error.status = response.status; throw error; } return response.json().catch(() => ({accepted: true})); }
function setBusy(busy) { rebootButton.disabled = busy; shutdownButton.disabled = busy; executeButton.disabled = busy; }
function openConfirmation(action) { requestedAction = action; confirmMessage.textContent = actions[action].confirm; confirmDialog.showModal(); }
function reconnectAfterReboot(attempts = 0) { if (attempts >= 24) return; window.setTimeout(async () => { try { const response = await fetch("/health", {cache: "no-store"}); if (response.ok) { window.location.assign("/display"); return; } } catch (_) {} reconnectAfterReboot(attempts + 1); }, 5000); }
async function executeAction() { if (!requestedAction) return; const action = actions[requestedAction]; confirmDialog.close(); setBusy(true); message.textContent = action.progress; try { await api(action.path); } catch (error) { if (error.status && error.status !== 503) { message.textContent = `操作を実行できませんでした: ${error.message}`; setBusy(false); return; } } if (requestedAction === "reboot") reconnectAfterReboot(); }

rebootButton.addEventListener("click", () => openConfirmation("reboot"));
shutdownButton.addEventListener("click", () => openConfirmation("shutdown"));
cancelButton.addEventListener("click", () => { requestedAction = null; confirmDialog.close(); });
executeButton.addEventListener("click", executeAction);
