import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_logical_id_ui_uses_explicit_editor_flow():
    script = (ROOT / "app/static/admin.js").read_text()
    assert 'class="node-logical-id"' not in script
    assert 'Logical IDを登録' in script
    assert 'Logical IDを変更' in script
    assert 'appendAtEnd: true' in script
    assert 'Logical IDを登録しました。' in script
    assert 'Logical IDを変更しました。' in script
    assert 'ACK' not in script
    assert 'nodes.addEventListener?.("focusin"' not in script
    assert 'onConfirm: value => { const editor = logicalIdEditor; logicalIdEditor = undefined; if (editor) submitNodeRegistration(editor.nodeId, value.trim(), editor.changing); }' in script
    assert 'api(`/nodes/${encodeURIComponent(nodeId)}/register`, {method: "POST", body: JSON.stringify({logical_id: logicalId})})' in script


def test_keyboard_confirm_and_cancel_callbacks():
    keyboard = ROOT / "app/static/software_keyboard.js"
    harness = r'''
const assert = require('node:assert/strict'), fs = require('fs'), vm = require('vm');
function element() { return {hidden: true, value: '', children: [], addEventListener(type, fn) { this[type] = fn; }, append(child) { this.children.push(child); }, set textContent(value) { this.content = value; }, get textContent() { return this.content || ''; }}; }
global.window = globalThis; global.document = {addEventListener() {}, createElement: element};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const elements = Object.fromEntries(['overlay', 'keys', 'title', 'value', 'count', 'cancel', 'confirm'].map(key => [key, element()]));
let confirmed, cancelled = 0; const input = element(); input.value = 'sen66-001'; input.setSelectionRange = () => {};
const keyboard = createSoftwareKeyboard(elements, {keyRows: () => [['x']], showClear: false});
keyboard.open(input, {label: 'Logical ID', length: 48, appendAtEnd: true, onConfirm: value => { confirmed = value; }, onCancel: () => { cancelled++; }});
keyboard.backspace(); assert.equal(input.value, 'sen66-00'); elements.confirm.click(); assert.equal(confirmed, 'sen66-00'); assert(elements.overlay.hidden);
keyboard.open(input, {label: 'Logical ID', length: 48, appendAtEnd: true, onCancel: () => { cancelled++; }}); elements.cancel.click(); assert.equal(cancelled, 1); assert(elements.overlay.hidden);
'''
    subprocess.run(["node", "-e", harness, str(keyboard)], check=True, capture_output=True, text=True)


def test_logical_keyboard_asset_versions():
    template = (ROOT / "app/templates/admin_sensors.html").read_text()
    for asset in ("display.css", "software_keyboard.js", "admin.js"):
        version = "20260916-logical-id-flow-1" if asset == "display.css" else "20260918-sensor-keyboard-1"
        assert re.search(re.escape(asset) + r"'\) }}\?v=" + version + r'"', template)


def test_sensor_keyboard_registration_edit_cancel_native_input_and_limits():
    script = (ROOT / "app/static/admin.js").read_text()
    wiring = script[script.index("// Put the editor inside"):script.index("function formatApiError")]
    harness = r'''
const assert = require('node:assert/strict'), fs = require('fs'), vm = require('vm');
function element() { return {hidden: true, value: '', disabled: false, maxLength: 64, children: [], listeners: {}, selectionStart: 0, selectionEnd: 0,
  addEventListener(type, fn) { this.listeners[type] = fn; }, append(child) { this.children.push(child); child.parentElement = this; },
  focus() {}, dispatchEvent() {}, setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }}; }
const elements = new Map(), documentListeners = [];
global.window = globalThis;
global.document = {querySelector(key) { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); }, createElement: element,
 addEventListener(type, fn) { documentListeners.push(fn); }};
const dialog = element(), editDialog = element();
const ids = ['sensor-id', 'display-name', 'location', 'edit-sensor-id', 'edit-display-name', 'edit-location'];
for (const id of ids) { const input = document.querySelector('#' + id); input.parentElement = element(); input.closest = () => id.startsWith('edit-') ? editDialog : dialog; }
const LOGICAL_ID_KEY_ROWS = [['1', '-'], ['a', 'b']];
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
'''
    harness += wiring + r'''
for (const id of ids) {
  const input = document.querySelector('#' + id), button = input.parentElement.children[0];
  input.value = 'original';
  assert.equal(button.type, 'button'); button.listeners.click();
  assert.equal(input.closest().children.at(-1), sensorKeyboardOverlay);
  assert(!sensorKeyboardOverlay.hidden);
  sensorKeyboard.clear(); sensorKeyboard.insert('a'); sensorKeyboard.insert('-'); sensorKeyboard.insert('1');
  document.querySelector('#sensor-keyboard-confirm').listeners.click();
  assert.equal(input.value, 'a-1'); assert(sensorKeyboardOverlay.hidden);
  button.listeners.click(); sensorKeyboard.clear(); sensorKeyboard.insert('x');
  document.querySelector('#sensor-keyboard-cancel').listeners.click(); assert.equal(input.value, 'a-1');
  button.listeners.click();
  // Browser/IME edits stay native and survive layout refresh and confirmation.
  sensorKeyboardInput.value = 'リビング'; sensorKeyboardInput.setSelectionRange(4, 4);
  documentListeners.forEach(fn => fn({key: 'ArrowLeft', preventDefault() { throw Error('native key intercepted'); }}));
  sensorKeyboard.refresh(); sensorKeyboard.insert(' ');
  document.querySelector('#sensor-keyboard-confirm').listeners.click(); assert.equal(input.value, 'リビング ');
  button.listeners.click(); sensorKeyboard.clear();
  for (let i = 0; i < 70; i++) sensorKeyboard.insert('a');
  assert.equal(sensorKeyboardInput.value.length, 64);
  sensorKeyboardInput.setSelectionRange(0, 64); sensorKeyboard.insert('b'); assert.equal(sensorKeyboardInput.value, 'b');
  let prevented = false;
  input.closest().listeners.cancel({preventDefault() { prevented = true; }});
  assert(prevented && sensorKeyboardOverlay.hidden); assert.equal(input.value, 'リビング ');
  // Outside the software editor the native field remains editable.
  input.value = 'physical'; assert(!input.disabled && !input.readOnly);
}
'''
    subprocess.run(["node", "-e", harness, str(ROOT / "app/static/software_keyboard.js")], check=True, capture_output=True, text=True)
    template = (ROOT / "app/templates/admin_sensors.html").read_text()
    for prefix in ("", "edit-"):
        assert re.search(r'id="' + prefix + r'sensor-id"[^>]*required pattern="\[a-z\]\[a-z0-9-\]\{0,63\}"', template)
        for field in ("display-name", "location"):
            assert re.search(r'id="' + prefix + field + r'"[^>]*maxlength="64"', template)
