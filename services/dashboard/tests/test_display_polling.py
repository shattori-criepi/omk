import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for display polling tests")
@pytest.mark.parametrize("demo_mode", ["off", "custom", "recommended", "clock"])
@pytest.mark.parametrize("failure", ["headers", "body", "network", "http", "json"])
def test_display_polling_recovers_and_preserves_values(demo_mode, failure):
    script = Path(__file__).resolve().parents[1] / "app/static/display.js"
    harness = r'''
{
const assert = require("node:assert/strict"), fs = require("fs"), vm = require("vm");
const [demoMode, failure] = process.argv.slice(2);
const demoEnabled = demoMode !== "off", mode = demoEnabled ? demoMode : "custom";
const intervals = [], timers = new Map(), requests = [], warnings = [];
let timerId = 0, phase = "success", reading = "21.0";
const value = {textContent: "initial"}, updatedAt = {textContent: "initial"};
const classList = {add() {}, remove() {}};
const item = {classList, querySelector(selector) {
  return selector === '[data-role="value"]' ? value : null;
}};
global.document = {
  documentElement: {classList},
  body: {dataset: {demoEnabled: String(demoEnabled), demoMode, dashboardMode: mode}},
  querySelector(selector) {
    if (selector === '[data-item-id="temperature"]') return item;
    if (selector === '[data-block-id="environment"]') return item;
    if (selector === "#updated-at" || selector === "#clock-updated-at") return updatedAt;
    return null;
  },
};
global.CSS = {escape: value => value};
global.window = {
  setInterval(fn, delay) { intervals.push({fn, delay}); },
  location: {reload() {assert.fail("unexpected reload");}, assign() {assert.fail("unexpected navigation");}},
};
global.setTimeout = (fn, delay) => {timers.set(++timerId, {fn, delay}); return timerId;};
global.clearTimeout = id => timers.delete(id);
console.warn = (...args) => warnings.push(args);
function data() {
  const temperature = {id: "temperature", value: reading, freshness: "normal"};
  return {demo_enabled: demoEnabled, mode, updated_at: reading, supplemental: [temperature],
    blocks: [{id: "environment", primary: temperature, secondary: []}]};
}
function pending(signal) {
  return new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {once: true});
  });
}
global.fetch = (url, options) => {
  requests.push({url, options});
  if (phase === "failure" && failure === "headers") return pending(options.signal);
  if (phase === "failure" && failure === "network") return Promise.reject(new TypeError("Failed to fetch"));
  return Promise.resolve({
    ok: !(phase === "failure" && failure === "http"), status: 503,
    json() {
      if (phase === "failure" && failure === "body") return pending(options.signal);
      if (phase === "failure" && failure === "json") return Promise.reject(new SyntaxError("Invalid JSON"));
      return Promise.resolve(data());
    },
  });
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const flush = () => new Promise(resolve => setImmediate(resolve));
const inProgress = () => vm.runInThisContext("displayFetchInProgress");
async function completedPoll(poll) {
  let completed = false;
  const result = poll().then(() => {completed = true;});
  await flush();
  assert.equal(completed, true, "poll must settle");
  await result;
  assert.equal(inProgress(), false);
  assert.equal(timers.size, 0, "timer must be cleared");
}
(async () => {
  assert.deepEqual(intervals.map(entry => entry.delay), [1000, 10000]);
  const poll = intervals[1].fn;
  await completedPoll(poll);
  assert.equal(value.textContent, "21.0");
  assert.equal(updatedAt.textContent, "21.0");
  assert.equal(warnings.length, 0);

  // Exercise repeated failures as well as a single outage.
  for (let attempt = 0; attempt < 2; attempt++) {
    phase = "failure";
    if (failure === "headers" || failure === "body") {
      let completed = false;
      const result = poll().then(() => {completed = true;});
      await flush();
      assert.equal(inProgress(), true);
      assert.equal(completed, false);
      const count = requests.length;
      await poll();
      assert.equal(requests.length, count, "overlapping polls must be skipped");
      assert.equal(timers.size, 1);
      const timer = [...timers.values()][0];
      assert.equal(timer.delay, 7000);
      assert(timer.delay < intervals[1].delay);
      assert.equal(requests.at(-1).options.signal.aborted, false);
      timer.fn();
      await flush();
      assert.equal(requests.at(-1).options.signal.aborted, true);
      assert.equal(completed, true, "timeout must settle the poll");
      await result;
      assert.equal(inProgress(), false);
      assert.equal(timers.size, 0);
    } else {
      await completedPoll(poll);
    }
    assert.equal(value.textContent, "21.0", "failed poll must preserve reading");
    assert.equal(updatedAt.textContent, "21.0", "failed poll must preserve timestamp");
  }
  assert.equal(warnings.length, 2);
  phase = "success";
  reading = "22.5";
  await completedPoll(poll);
  assert.equal(value.textContent, "22.5");
  assert.equal(updatedAt.textContent, "22.5");
  assert.equal(requests.length, 4);
  for (const {url, options} of requests) {
    assert.equal(url, demoEnabled ? `/api/display?demo_mode=${demoMode}` : "/api/display");
    assert.equal(options.cache, "no-store");
    assert(options.signal instanceof AbortSignal);
  }
  assert.notEqual(requests[2].options.signal, requests[3].options.signal);
  assert.equal(requests[3].options.signal.aborted, false);
})().catch(error => {console.error(error); process.exitCode = 1;});
}
'''
    subprocess.run(
        ["node", "-e", harness, str(script), demo_mode, failure],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
