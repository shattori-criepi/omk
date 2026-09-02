const DATASETS = {
  broute_power: "Bルート 瞬時電力",
  broute_cumulative_energy: "Bルート 積算電力量",
  broute_interval_energy: "Bルート 30分電力量",
  sen66: "SEN66",
  ichijo_power_flow: "パワコン",
  ble_environment: "BLE 環境",
  ble_motion: "BLE 人感",
  ble_contact: "BLE 開閉",
  ble_power: "BLE 電力",
};

const USB_MESSAGES = {
  not_present: "USBメモリが見つかりません",
  ambiguous: "USBメモリを1つだけ接続してください",
  unsupported_filesystem: "FAT32 または exFAT のUSBメモリを接続してください",
  mount_not_writable: "USBメモリへ書き込めません",
  busy: "USBメモリを使用できません",
  export_failed: "書き出しに失敗しました",
  sync_failed: "USBメモリへの保存確認に失敗しました",
  unmount_failed: "USBメモリを安全に取り外せません",
};

const $ = (selector) => document.querySelector(selector);
const form = $("#export-form");
const actionButton = $("#export-button");
const usbStatus = $("#usb-status");
const jobStatus = $("#export-job-status");
const jobTitle = $("#export-job-title");
const jobDetail = $("#export-job-detail");
const datasets = $("#datasets");
const calendarDialog = $("#calendar-dialog");
const calendarGrid = $("#calendar-grid");

let selectedFrom;
let selectedTo;
let calendarTarget;
let calendarMonth;
let lastUsb = { state: "not_present" };
let lastExport = { state: "idle" };

function jstParts(date = new Date()) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return Object.fromEntries(formatter.formatToParts(date)
    .filter((part) => part.type !== "literal")
    .map((part) => [part.type, part.value]));
}

function jstDate(offset = 0) {
  const parts = jstParts();
  const date = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + offset));
  return date.toISOString().slice(0, 10);
}

function formatJapaneseDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function renderDates() {
  $("#from-display").textContent = formatJapaneseDate(selectedFrom);
  $("#to-display").textContent = formatJapaneseDate(selectedTo);
}

function selectedDatasetNames() {
  return [...datasets.querySelectorAll("input:checked")].map((input) => input.value);
}

function renderDatasets() {
  datasets.replaceChildren(...Object.entries(DATASETS).map(([value, label]) => {
    const option = document.createElement("label");
    option.className = "export-dataset-option";
    option.innerHTML = `<input type="checkbox" value="${value}" checked><span>${label}</span>`;
    return option;
  }));
}

function renderUsbStatus(usb) {
  const state = usb?.state || "not_present";
  if (state === "available" || state === "mounted") {
    const filesystem = usb.filesystem === "vfat" ? "FAT32" : usb.filesystem === "exfat" ? "exFAT" : "利用可能";
    usbStatus.textContent = `利用可能 / ${filesystem}`;
    return;
  }
  usbStatus.textContent = USB_MESSAGES[state] || "対応するUSBメモリを挿入してください";
}

function renderExportState(exportState) {
  const state = exportState?.state || "idle";
  jobStatus.className = `export-job-status export-job-status--${state}`;
  actionButton.textContent = state === "running" ? "書き出し中…" : "USBへ書き出す";

  if (state === "idle") {
    jobStatus.hidden = true;
    jobTitle.textContent = "";
    jobDetail.textContent = "";
    return;
  }

  jobStatus.hidden = false;
  if (state === "running") {
    jobTitle.textContent = "データを書き出しています…";
    jobDetail.textContent = "USBメモリを取り外さないでください";
  } else if (state === "succeeded") {
    jobTitle.textContent = "書き出しが完了しました";
    jobDetail.textContent = "USBメモリを取り外せます";
  } else if (exportState?.error_code === "unmount_failed") {
    jobTitle.textContent = "USBメモリを安全に取り外せません";
    jobDetail.textContent = "まだ取り外さないでください";
  } else {
    jobTitle.textContent = USB_MESSAGES[exportState?.error_code] || "書き出しに失敗しました";
    jobDetail.textContent = "必要に応じてもう一度お試しください";
  }
}

function updateControls() {
  const running = lastExport?.state === "running";
  const usbAvailable = ["available", "mounted"].includes(lastUsb?.state);
  const unsafeToRemove = lastExport?.error_code === "unmount_failed";
  actionButton.disabled = running || !usbAvailable || unsafeToRemove || selectedDatasetNames().length === 0;

  document.querySelectorAll("[data-preset], [data-date-target], #select-all, #select-none, #datasets input")
    .forEach((element) => { element.disabled = running; });
}

function renderCalendar() {
  const year = calendarMonth.getUTCFullYear();
  const month = calendarMonth.getUTCMonth();
  $("#calendar-title").textContent = `${year}年${month + 1}月`;
  calendarGrid.replaceChildren();
  const weekdays = ["日", "月", "火", "水", "木", "金", "土"];
  weekdays.forEach((weekday) => {
    const heading = document.createElement("span");
    heading.className = "calendar-weekday";
    heading.textContent = weekday;
    calendarGrid.append(heading);
  });

  const first = new Date(Date.UTC(year, month, 1));
  const firstDay = first.getUTCDay();
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  for (let i = 0; i < firstDay; i += 1) calendarGrid.append(document.createElement("span"));
  for (let day = 1; day <= days; day += 1) {
    const value = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.day = value;
    button.textContent = String(day);
    if (value === (calendarTarget === "from" ? selectedFrom : selectedTo)) button.classList.add("selected");
    if (value === jstDate()) button.classList.add("today");
    calendarGrid.append(button);
  }
}

function openCalendar(target) {
  calendarTarget = target;
  const current = target === "from" ? selectedFrom : selectedTo;
  const [year, month] = current.split("-").map(Number);
  calendarMonth = new Date(Date.UTC(year, month - 1, 1));
  renderCalendar();
  calendarDialog.showModal();
}

function applyPreset(preset) {
  const today = jstDate();
  selectedTo = today;
  if (preset === "today") selectedFrom = today;
  if (preset === "week") selectedFrom = jstDate(-7);
  if (preset === "month30") selectedFrom = jstDate(-30);
  if (preset === "month") selectedFrom = `${today.slice(0, 7)}-01`;
  renderDates();
}

async function refresh() {
  try {
    const response = await fetch("/api/export/usb/status");
    if (!response.ok) throw new Error("status request failed");
    const status = await response.json();
    lastUsb = status.usb || { state: "not_present" };
    lastExport = status.export || { state: "idle" };
    renderUsbStatus(lastUsb);
    renderExportState(lastExport);
    updateControls();
  } catch (_) {
    lastUsb = { state: "not_present" };
    lastExport = { state: "failed", error_code: "export_failed" };
    renderUsbStatus(lastUsb);
    renderExportState(lastExport);
    updateControls();
  }
}

document.querySelectorAll("[data-date-target]").forEach((button) => {
  button.addEventListener("click", () => openCalendar(button.dataset.dateTarget));
});
document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => applyPreset(button.dataset.preset));
});
$("#select-all").addEventListener("click", () => {
  datasets.querySelectorAll("input").forEach((input) => { input.checked = true; });
  updateControls();
});
$("#select-none").addEventListener("click", () => {
  datasets.querySelectorAll("input").forEach((input) => { input.checked = false; });
  updateControls();
});
datasets.addEventListener("change", updateControls);
$("#calendar-prev").addEventListener("click", () => {
  calendarMonth.setUTCMonth(calendarMonth.getUTCMonth() - 1);
  renderCalendar();
});
$("#calendar-next").addEventListener("click", () => {
  calendarMonth.setUTCMonth(calendarMonth.getUTCMonth() + 1);
  renderCalendar();
});
$("#calendar-cancel").addEventListener("click", () => calendarDialog.close());
calendarGrid.addEventListener("click", (event) => {
  const value = event.target.dataset.day;
  if (!value) return;
  if (calendarTarget === "from") {
    selectedFrom = value;
    if (selectedFrom > selectedTo) selectedTo = selectedFrom;
  } else {
    selectedTo = value;
    if (selectedTo < selectedFrom) selectedFrom = selectedTo;
  }
  renderDates();
  calendarDialog.close();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = selectedDatasetNames();
  if (!selected.length || actionButton.disabled) return;
  lastExport = { state: "running" };
  renderExportState(lastExport);
  updateControls();
  try {
    const response = await fetch("/api/export/usb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from: selectedFrom, to: selectedTo, datasets: selected }),
    });
    if (!response.ok) throw new Error("export request failed");
    await refresh();
  } catch (_) {
    lastExport = { state: "failed", error_code: "export_failed" };
    renderExportState(lastExport);
    updateControls();
  }
});

renderDatasets();
applyPreset("week");
refresh();
window.setInterval(refresh, 3000);
