document.documentElement.classList.add("js-enabled");

const currentDatetime = document.querySelector("#current-datetime");

function updateCurrentDatetime() {
  if (!currentDatetime) {
    return;
  }

  const now = new Date();

  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);

  const values = Object.fromEntries(
    parts.map(({ type, value }) => [type, value]),
  );

  const weekday = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    weekday: "short",
  }).format(now);

  const time = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);

  currentDatetime.textContent =
    `${values.year}/${values.month}/${values.day}(${weekday}) ${time}`;
  currentDatetime.dateTime = now.toISOString();
}

updateCurrentDatetime();
window.setInterval(updateCurrentDatetime, 1000);
