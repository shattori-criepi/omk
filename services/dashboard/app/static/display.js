document.documentElement.classList.add("js-enabled");

const currentDatetime = document.querySelector("#current-datetime");
const headerDateMain = document.querySelector("#header-date-main");
const headerWeekday = document.querySelector("#header-weekday");
const headerTime = document.querySelector("#header-time");
const clockDate = document.querySelector("#clock-date");
const clockTime = document.querySelector("#clock-time");
const clockUpdatedAt = document.querySelector("#clock-updated-at");
const clockFreshness = document.querySelector("#clock-freshness");
const DISPLAY_POLL_INTERVAL_MS = 10_000;
const WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_ARIA_NAMES = ["日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日"];
let displayFetchInProgress = false;
const demoEnabled = document.body?.dataset.demoEnabled === "true";
const demoModes = ["custom", "clock", "recommended"];
let demoModeIndex = Math.max(0, demoModes.indexOf(document.body?.dataset.demoMode));

function updateCurrentDatetime() {
  if (!currentDatetime && !clockDate && !clockTime) return;

  const now = new Date();
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  const weekdayIndex = new Date(Date.UTC(
    Number(values.year), Number(values.month) - 1, Number(values.day),
  )).getUTCDay();
  const weekday = WEEKDAY_NAMES[weekdayIndex];
  const time = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(now);
  const dateText = `${values.year}/${values.month}/${values.day}`;
  if (headerDateMain) headerDateMain.textContent = dateText;
  if (headerWeekday) headerWeekday.textContent = weekday;
  if (headerTime) headerTime.textContent = ` ${time}`;
  const clockTimeText = new Intl.DateTimeFormat("ja-JP", {timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hour12: false}).format(now);
  if (clockDate) clockDate.textContent = `${values.year}年${Number(values.month)}月${Number(values.day)}日 ${WEEKDAY_ARIA_NAMES[weekdayIndex]}`;
  if (clockTime) { clockTime.textContent = clockTimeText; clockTime.dateTime = now.toISOString(); }
  if (currentDatetime) {
    currentDatetime.setAttribute(
      "aria-label",
      `${values.year}年${values.month}月${values.day}日 ${WEEKDAY_ARIA_NAMES[weekdayIndex]} ${time}`,
    );
    currentDatetime.dateTime = now.toISOString();
  }
}

function setText(id, value) {
  const element = document.querySelector(`#${id}`);
  if (element) element.textContent = value;
}

function sourceBadgeText(freshness) {
  return freshness === "delayed" ? "遅延" : freshness === "unavailable" ? "取得不可" : "";
}

function displayUnitText(item) {
  // Keep the unit grid row present for unitless and unavailable readings.  The
  // server-rendered strip markup uses a non-breaking space for the same reason.
  return item.unit && item.freshness !== "unavailable" ? item.unit : "\u00a0";
}

function updateSourceStatus(sectionId, badgeId, freshness) {
  const section = document.querySelector(`#${sectionId}`);
  if (section) {
    section.classList.remove("source--normal", "source--delayed", "source--unavailable");
    section.classList.add(`source--${freshness}`);
    section.querySelectorAll(".source-unit").forEach((unit) => {
      unit.hidden = freshness === "unavailable";
    });
  }

  const badge = document.querySelector(`#${badgeId}`);
  if (badge) {
    badge.textContent = sourceBadgeText(freshness);
    badge.hidden = freshness === "normal";
    badge.className = `source-badge source-badge--${freshness}`;
  }
}

function renderedDisplayMode() {
  if (document.querySelector("#clock-supplemental")) return "clock";
  if (document.querySelector("#display-blocks")) return "blocks";
  if (document.querySelector("#power-section")) return "legacy";
  return "unknown";
}

function responseDisplayMode(data) {
  if (data.mode === "clock" && Array.isArray(data.supplemental)) return "clock";
  if (["standard", "custom", "recommended"].includes(data.mode) && Array.isArray(data.blocks)) return "blocks";
  return "legacy";
}

function updateDisplay(data) {
  // A newly discovered catalog can change the server from the legacy template
  // to Block Dashboard between page load and the first poll. Their DOM shapes
  // differ, so updating values in place would leave old stale badges visible.
  // Reload once to render the matching template; ordinary freshness changes
  // continue through the in-place paths below.
  const renderedMode = renderedDisplayMode();
  if (renderedMode !== "unknown" && renderedMode !== responseDisplayMode(data)) {
    if (demoEnabled) window.location.assign(`/display?demo_mode=${demoModes[demoModeIndex]}`);
    else window.location.reload();
    return;
  }
  if (data.mode === "clock" && Array.isArray(data.supplemental)) {
    if (clockDate && data.date) clockDate.textContent = data.date;
    if (clockTime && data.time) clockTime.textContent = data.time;
    if (clockUpdatedAt) { clockUpdatedAt.textContent = data.updated_at; clockUpdatedAt.dateTime = data.updated_at_iso; }
    if (clockFreshness) { clockFreshness.textContent = data.freshness; clockFreshness.className = `freshness freshness--${data.freshness}`; }
    data.supplemental.forEach((item) => {
      const reading = document.querySelector(`[data-item-id="${CSS.escape(item.id)}"]`);
      if (!reading) return;
      const label = reading.querySelector('[data-role="label"]');
      const value = reading.querySelector('[data-role="value"]');
      const unit = reading.querySelector('[data-role="unit"]');
      if (label) label.textContent = item.short_label || item.label;
      if (value) value.textContent = item.value;
      if (unit) { unit.textContent = displayUnitText(item); unit.hidden = false; }
      reading.className = `clock-reading clock-reading--${item.freshness}`;
    });
    return;
  }
  if (["standard", "custom", "recommended"].includes(data.mode) && Array.isArray(data.blocks)) {
    data.blocks.forEach((block) => {
      const card = document.querySelector(`[data-block-id="${CSS.escape(block.id)}"]`);
      if (!card) return;
      const items = [block.primary, ...(block.secondary || [])];
      items.forEach((item) => {
        const itemCard = document.querySelector(`[data-item-id="${CSS.escape(item.id)}"]`);
        if (!itemCard) return;
        const label = itemCard.querySelector('[data-role="label"]');
        const value = itemCard.querySelector('[data-role="value"]');
        const unit = itemCard.querySelector('[data-role="unit"]');
        if (label) label.textContent = item.short_label || item.label;
        if (value) value.textContent = item.value;
        if (unit) {
          unit.textContent = displayUnitText(item);
          unit.hidden = false;
        }
        itemCard.classList.remove("display-card--normal", "display-card--delayed", "display-card--unavailable");
        itemCard.classList.add(`display-card--${item.freshness}`);
      });
      const auxiliary = card.querySelector('[data-role="auxiliary"]');
      if (auxiliary) {
        const hasAuxiliary = Boolean(block.auxiliary_label);
        auxiliary.hidden = !hasAuxiliary;
        if (hasAuxiliary) {
          auxiliary.classList.remove("display-card-auxiliary--purchase", "display-card-auxiliary--sale");
          auxiliary.classList.add(`display-card-auxiliary--${block.auxiliary_flow}`);
          auxiliary.querySelector('[data-role="auxiliary-label"]').textContent = block.auxiliary_label;
          auxiliary.querySelector('[data-role="auxiliary-value"]').textContent = block.auxiliary_value;
          auxiliary.querySelector('[data-role="auxiliary-unit"]').textContent = block.auxiliary_unit;
        }
      }
      const badge = card.querySelector('[data-role="freshness"]');
      if (badge) badge.textContent = sourceBadgeText(block.freshness);
      card.classList.remove("display-card--normal", "display-card--delayed", "display-card--unavailable");
      card.classList.add(`display-card--${block.freshness}`);
    });
    const updatedAt = document.querySelector("#updated-at");
    if (updatedAt) { updatedAt.textContent = data.updated_at; updatedAt.dateTime = data.updated_at_iso; }
    const freshness = document.querySelector("#freshness");
    if (freshness) { freshness.textContent = data.freshness; freshness.className = `freshness freshness--${data.freshness}`; }
    return;
  }
  for (const [id, value] of Object.entries({
    "current-power-label": data.current_power_label,
    "current-power-kw": data.current_power_kw,
    "power-direction": data.power_direction,
    "grid-flow-label": data.grid_flow_label,
    "grid-flow-kw": data.grid_flow_kw,
    "pv-power-kw": data.pv_power_kw,
    "sold-today-kwh": data.sold_today_kwh,
    "battery-soc-percent": data.battery_soc_percent,
    "battery-power-label": data.battery_power_label,
    "battery-power-kw": data.battery_power_kw,
    "purchased-today-kwh": data.purchased_today_kwh,
    "temperature-c": data.temperature_c,
    "humidity-percent": data.humidity_percent,
    "co2-ppm": data.co2_ppm,
    "pm25-ug-m3": data.pm25_ug_m3,
    "voc-index": data.voc_index,
  })) setText(id, value);

  const updatedAt = document.querySelector("#updated-at");
  if (updatedAt) {
    updatedAt.textContent = data.updated_at;
    updatedAt.dateTime = data.updated_at_iso;
  }

  const freshness = document.querySelector("#freshness");
  if (freshness) {
    freshness.textContent = data.freshness;
    freshness.className = `freshness freshness--${data.freshness}`;
    freshness.setAttribute("aria-label", `データ鮮度: ${data.freshness}`);
  }

  const direction = document.querySelector("#power-direction");
  if (direction) {
    direction.hidden = !data.power_direction;
    direction.className = `power-direction power-direction--${data.power_flow}`;
  }

  const hasIchijo = data.has_ichijo_power_flow;
  const gridFlow = document.querySelector("#grid-flow");
  const powerDetails = document.querySelector("#power-details");
  if (gridFlow) {
    gridFlow.hidden = !hasIchijo;
    gridFlow.className = `grid-flow grid-flow--${data.grid_flow}`;
  }
  if (powerDetails) powerDetails.hidden = !hasIchijo;

  updateSourceStatus("power-section", "power-source-badge", data.power_freshness);
  updateSourceStatus("sen66-section", "sen66-source-badge", data.sen66_freshness);
}

async function refreshDisplay() {
  if (displayFetchInProgress) return;
  displayFetchInProgress = true;
  try {
    const response = demoEnabled
      ? await fetch(`/api/display?demo_mode=${demoModes[demoModeIndex]}`, { cache: "no-store" })
      : await fetch("/api/display", { cache: "no-store" });
    if (!response.ok) throw new Error(`display API returned ${response.status}`);
    updateDisplay(await response.json());
  } catch (error) {
    console.warn("Dashboard refresh failed; keeping current values.", error);
  } finally {
    displayFetchInProgress = false;
  }
}

updateCurrentDatetime();
window.setInterval(updateCurrentDatetime, 1_000);
window.setInterval(() => {
  if (demoEnabled) demoModeIndex = (demoModeIndex + 1) % demoModes.length;
  refreshDisplay();
}, DISPLAY_POLL_INTERVAL_MS);
