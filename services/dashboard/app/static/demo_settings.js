/* Isolated from the main editor so toggling demo mode cannot overwrite presets. */
document.addEventListener("DOMContentLoaded", async () => {
  const input = document.querySelector("#demo-enabled");
  const status = document.querySelector("#settings-status");
  if (!input) return;
  try {
    const response = await fetch("/api/admin/dashboard-settings", {cache: "no-store"});
    if (!response.ok) throw new Error();
    input.checked = Boolean((await response.json()).demo?.enabled);
  } catch { if (status) status.textContent = "展示用デモモードの設定を取得できません"; }
  input.addEventListener("change", async () => {
    const enabled = input.checked;
    try {
      const response = await fetch("/api/admin/dashboard-settings/demo", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled})});
      if (!response.ok) throw new Error();
      if (status) status.textContent = "展示用デモモードを保存しました";
    } catch { input.checked = !enabled; if (status) status.textContent = "展示用デモモードを保存できません"; }
  });
});
