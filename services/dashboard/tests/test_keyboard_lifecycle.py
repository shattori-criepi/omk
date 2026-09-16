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
        version = "20260916-reinitialize" if asset == "admin.js" else "20260916-logical-id-flow-1"
        assert re.search(re.escape(asset) + r"'\) }}\?v=" + version + r'"', template)
