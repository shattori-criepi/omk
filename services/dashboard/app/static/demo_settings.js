/* Isolated from the main editor so toggling demo mode cannot overwrite presets. */
document.addEventListener("DOMContentLoaded", async () => {
  const input = document.querySelector("#demo-enabled");
  const status = document.querySelector("#settings-status");
  const entry = document.querySelector("#demo-entry");
  const panel = document.querySelector("#demo-settings");
  if (!input) return;
  const showDemoState = () => { if (entry) entry.textContent = input.checked ? "デモ用（使用中）" : "デモ用"; };
  if (entry && panel) entry.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    entry.setAttribute("aria-expanded", String(!panel.hidden));
  });
  const modeRoot = document.querySelector("#display-modes");
  if (modeRoot && entry && panel) modeRoot.addEventListener("click", event => {
    if (!event.target.closest("[data-mode]")) return;
    panel.hidden = true;
    entry.setAttribute("aria-expanded", "false");
  });
  try {
    const response = await fetch("/api/admin/dashboard-settings", {cache: "no-store"});
    if (!response.ok) throw new Error();
    input.checked = Boolean((await response.json()).demo?.enabled);
    showDemoState();
  } catch { if (status) status.textContent = "展示用デモモードの設定を取得できません"; }
  input.addEventListener("change", async () => {
    const enabled = input.checked;
    input.disabled = true;
    try {
      const response = await fetch("/api/admin/dashboard-settings/demo", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
      if (!response.ok) throw new Error();
      if (status) status.textContent = "展示用デモモードを保存しました";
    } catch { input.checked = !enabled; if (status) status.textContent = "展示用デモモードを保存できません"; }
    finally { input.disabled = false; showDemoState(); }
  });
});
