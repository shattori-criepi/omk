import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_keyboard_lifecycle_and_node_poll():
    script = (ROOT / 'app/static/admin.js').read_text()
    logical = script[script.index('const LOGICAL_ID_KEY_ROWS'):script.index('function formatApiError')]
    rendering = script[script.index('function nodeCard'):script.index('function stopRegistrationPoll')]
    focus_handler = next(line for line in script.splitlines() if line.startswith('nodes.addEventListener?.("focusin"'))
    harness = r'''
const assert = require('node:assert/strict'), fs = require('fs'), vm = require('vm');
function element() {
  return {value: '', disabled: false, hidden: true, children: [], listeners: {}, dataset: {}, selectionStart: 0, selectionEnd: 0,
    set textContent(value) { this.content = value; this.children = []; }, get textContent() { return this.content || ''; },
    addEventListener(type, fn) { this.listeners[type] = fn; }, append(child) { this.children.push(child); },
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    matches(selector) { return selector === '.node-logical-id'; },
    focus() { document.activeElement = this; nodes.listeners.focusin({target: this}); }
  };
}
const elements = {}, documentListeners = {};
const nodes = element(); let inputs = [];
nodes.querySelectorAll = selector => selector === '.node-logical-id' ? inputs : [];
nodes.querySelector = selector => selector.startsWith('.node-logical-id') ? inputs.find(input => selector.includes(`"${input.dataset.nodeId}"`)) : null;
Object.defineProperty(nodes, 'innerHTML', {set(html) {
  document.activeElement = null;
  inputs = [...html.matchAll(/<input class="node-logical-id"[^>]*>/g)].map(([tag]) => {
    const input = element(); input.dataset.nodeId = tag.match(/data-node-id="([^"]+)"/)[1];
    input.value = tag.match(/value="([^"]*)"/)?.[1] || ''; return input;
  });
}});
global.window = globalThis;
global.document = {activeElement: null, querySelector: selector => elements[selector] ||= element(), createElement: element,
  addEventListener(type, fn) { documentListeners[type] = fn; }};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
let latestNodes = [], usbCandidatesByNodeId = new Map(), usbCandidatesInitialized = true, usbProvisioningInProgress = false, pendingNodeRegistration;
const text = value => String(value ?? '—'), statusLine = {}, requests = [];
let serverNodes = ['09dda0d5a8f2', '000000000002'].map(node_id => ({node_id, registration_state: 'registered', logical_id: 'original', online: true}));
async function api(path, options) { requests.push({path, options}); return {nodes: path === '/nodes' ? serverNodes : []}; }
''' + logical + rendering + focus_handler + r'''
const overlay = elements['#logical-id-keyboard-overlay'], keys = elements['#logical-id-keyboard-keys'];
const labels = () => keys.children.flatMap(row => row.children.map(button => button.textContent));
function tap(label) { const button = keys.children.flatMap(row => row.children).find(button => button.textContent === label); assert(button, label); button.listeners.click(); }
function physical(key) { let prevented = false; documentListeners.keydown({key, preventDefault() { prevented = true; }}); return prevented; }
const targetId = '09dda0d5a8f2';
(async () => {
  // No key rendering or overlay changes before the first open.
  logicalKeyboard.refresh(); assert.equal(keys.children.length, 0); assert(overlay.hidden);
  await loadNodes(); assert(overlay.hidden);
  let input = logicalInputForNode(targetId); input.value = ''; input.focus();
  assert(!overlay.hidden);
  assert.deepEqual(keys.children[3].children.map(button => button.textContent), ['z', 'x', 'c', 'v', 'b', 'n', 'm', '大文字', '⌫']);
  assert(!labels().includes('全消去'));
  for (const key of 'sen66-001') tap(key);
  assert.equal(input.value, 'sen66-001');
  tap('大文字'); assert(labels().includes('S')); assert(!labels().includes('s'));
  const oldRows = keys.children;
  logicalKeyboard.refresh(); assert.notEqual(keys.children, oldRows); assert(labels().includes('S'));
  // The poll replaces both inputs, reverses their order, and restores focus (firing focusin).
  serverNodes.reverse(); await loadNodes();
  const replacement = logicalInputForNode(targetId);
  assert.notEqual(replacement, input); assert.equal(replacement.value, 'sen66-001');
  assert.equal(document.activeElement, replacement); assert(labels().includes('S'));
  tap('A'); assert.equal(replacement.value, 'sen66-001A');
  assert.equal(logicalInputForNode('000000000002').value, 'original');
  tap('⌫'); assert.equal(replacement.value, 'sen66-001');
  tap('小文字'); assert(labels().includes('s')); assert(!labels().includes('S'));
  assert(physical('B')); assert.equal(replacement.value, 'sen66-001B');
  physical('Backspace'); assert.equal(replacement.value, 'sen66-001');
  elements['#logical-id-keyboard-cancel'].listeners.click();
  assert.equal(replacement.value, ''); assert(overlay.hidden);
  // Focus restoration must not reopen a cancelled keyboard.
  await loadNodes(); assert(overlay.hidden); assert.equal(logicalInputForNode(targetId).value, '');
  logicalInputForNode(targetId).focus();
  assert(labels().includes('大文字')); tap('大文字'); tap('S');
  elements['#logical-id-keyboard-confirm'].listeners.click(); assert(overlay.hidden);
  await loadNodes(); assert(overlay.hidden); assert.equal(logicalInputForNode(targetId).value, 'S');
  logicalInputForNode(targetId).focus(); assert(labels().includes('大文字')); assert(labels().includes('s'));
  physical('Escape'); assert(overlay.hidden);
  const oldKeys = keys.children; logicalKeyboard.refresh(); assert.equal(keys.children, oldKeys);
  assert.equal(physical('a'), false);
  assert(requests.every(request => request.options === undefined)); // Confirmation never registers a Node.
  assert(!validLogicalId('')); assert(validLogicalId('a'.repeat(48))); assert(!validLogicalId('a'.repeat(49))); assert(!validLogicalId('a!'));
  input = logicalInputForNode(targetId); input.disabled = true; openLogicalKeyboard(input); assert(overlay.hidden);
  input.disabled = false; openLogicalKeyboard(input); input.disabled = true; logicalKeyboard.refresh(); assert(overlay.hidden);
  input.disabled = false; openLogicalKeyboard(input); usbProvisioningInProgress = true;
  await loadNodes(); assert(overlay.hidden); assert.equal(inputs.length, 0);
  openLogicalKeyboard(input); assert(overlay.hidden);
  usbProvisioningInProgress = false; await loadNodes(); assert(overlay.hidden);
  logicalInputForNode(targetId).focus(); serverNodes = serverNodes.filter(node => node.node_id !== targetId);
  await loadNodes(); assert(overlay.hidden); // Missing target must not fall back to another Node.
  assert.equal(logicalInputForNode('000000000002').value, 'original');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(['node', '-e', harness, str(ROOT / 'app/static/software_keyboard.js')], check=True, capture_output=True, text=True, timeout=10)


def test_keyboard_initial_state_is_explicit_and_closed_refresh_skips_callbacks():
    harness = r'''
const assert = require('node:assert/strict'), fs = require('fs'), vm = require('vm');
global.window = globalThis;
const element = () => ({hidden: true, value: '', addEventListener() {}, append() {}});
global.document = {addEventListener() {}, createElement: element};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const elements = Object.fromEntries(['overlay', 'keys', 'title', 'value', 'count', 'cancel', 'confirm'].map(key => [key, element()]));
let calls = 0;
const keyboard = createSoftwareKeyboard(elements, {keyRows(state) { calls++; assert.equal(state.uppercase, false); return []; }});
keyboard.refresh(); assert.equal(calls, 0); assert(elements.overlay.hidden);
keyboard.open(element(), {label: 'Logical ID', length: 48}); assert.equal(calls, 1);
keyboard.refresh(); assert.equal(calls, 2);
keyboard.close(false); keyboard.refresh(); assert.equal(calls, 2); assert(elements.overlay.hidden);
'''
    subprocess.run(['node', '-e', harness, str(ROOT / 'app/static/software_keyboard.js')], check=True, capture_output=True, text=True, timeout=10)


def test_logical_keyboard_asset_versions():
    template = (ROOT / 'app/templates/admin_sensors.html').read_text()
    for asset in ('display.css', 'software_keyboard.js', 'admin.js'):
        assert re.search(re.escape(asset) + r"'\) }}\?v=20260916-logical-id-keyboard-2\"", template)
