import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_usb_setup_ui_contract_and_token_boundary():
    template = (ROOT/'app/templates/admin_sensors.html').read_text()
    script = (ROOT/'app/static/admin.js').read_text()
    assert re.search(r"admin\.js'\) }}\?v=[^\"']+", template)
    for expected in ('OMK Nodeをセットアップ', '/setup/usb-setup', '/setup/usb-setup/status', 'confirm_atom_s3_lite: confirmed', 'pollUsbSetup();'):
        assert expected in script
    assert 'Bearer ' not in script and 'OMK_SYSTEM_MANAGER_TOKEN' not in script
    assert 'confirmUsbProvisionFailure' not in script  # Wi-Fi configured is not setup success.
    backend = (ROOT/'app/main.py').read_text()
    assert '"POST", "/api/nodes/usb-setup", await request.json()' in backend
    assert '"GET", "/api/nodes/usb-setup/status"' in backend


def test_cards_and_explicit_new_node_confirmation():
    # Execute the actual rendering and submit functions with a small DOM stub.
    script = (ROOT/'app/static/admin.js').read_text()
    rendering = script[script.index('function nodeCard'):script.index('function saveNodeInputState')]
    submit = script[script.index('async function submitUsbProvision'):script.index('// Restore progress')]
    javascript = r'''
const assert = require('node:assert/strict');
const text = value => String(value ?? '').replaceAll('<', '&lt;');
let usbProvisioningInProgress = false, pendingNodeRegistration, usbSetupPoll;
const usbCandidatesByNodeId = new Map();
const statusLine = {};
let requests = [];
const api = async (path, options) => requests.push({path, body: JSON.parse(options.body)});
const pollUsbSetup = async () => {};
const renderNodes = () => {};
''' + rendering + submit + r'''
const candidate = {device: '/dev/ttyACM0', node_id: '000000000001', kind: 'omk_node', wifi_configured: true};
usbCandidatesByNodeId.set(candidate.node_id, candidate);
let card = nodeCard({node_id: candidate.node_id, registration_state: 'registered', logical_id: 'sen66-001', capabilities: ['ble_scan', 'sen66']});
assert(card.includes('OMK Nodeをセットアップ'));
assert(card.includes('Gatewayに配置済みのfirmwareを書き込み、Wi-Fiを設定します。'));
assert(!card.includes('毎回、Gatewayに配置済み'));
assert(card.includes('対応機能: BLE relay対応 · SEN66対応'));
assert(card.includes('sen66-001'));
candidate.kind = 'unconfirmed_esp32s3';
card = nodeCard(candidate);
assert(card.includes('ESP32-S3を検出') && card.includes('class="confirm-atom"'));
assert(card.includes('対応機能: セットアップ後に確認'));
const button = {disabled: false, dataset: {device: candidate.device, nodeId: candidate.node_id, unconfirmed: 'true'}, closest: () => ({querySelector: () => ({checked: false})})};
(async () => {
 await submitUsbProvision(button);
 assert.equal(requests.length, 0);
 button.closest = () => ({querySelector: () => ({checked: true})});
 await submitUsbProvision(button);
 assert.deepEqual(requests[0], {path: '/setup/usb-setup', body: {device: candidate.device, node_id: candidate.node_id, confirm_atom_s3_lite: true}});
 await submitUsbProvision(button);
 assert.equal(requests.length, 1);
 candidate.kind = 'recovery_required';
 assert(!usbSetupControls(candidate).includes('<button'));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', javascript], check=True, capture_output=True, text=True)


def test_atom_confirmation_is_preserved_and_locked_while_setup_runs():
    script = (ROOT/'app/static/admin.js').read_text()
    controls = script[script.index('function usbSetupControls'):script.index('function validLogicalId')]
    harness = r'''
const assert = require('node:assert/strict');
const text = value => String(value ?? '—');
let usbProvisioningInProgress = false;
const logicalInputs = [];
const oldConfirmation = {checked: true, dataset: {nodeId: '000000000001'}};
const newConfirmation = {checked: false, disabled: true, dataset: {nodeId: '000000000001'}};
const nodes = {
  querySelectorAll(selector) { return selector === '.confirm-atom' ? [oldConfirmation] : logicalInputs; },
  querySelector(selector) { return selector.startsWith('.confirm-atom') ? newConfirmation : null; },
};
global.document = {activeElement: null};
''' + controls + r'''
const candidate = {device: '/dev/ttyACM0', node_id: '000000000001', kind: 'unconfirmed_esp32s3'};
const saved = saveNodeInputState();
assert.equal(saved[candidate.node_id].confirmAtom, true);
usbProvisioningInProgress = true;
restoreNodeInputState(saved);
const controlsDuringSetup = usbSetupControls(candidate);
assert.equal(newConfirmation.checked, true);
assert(controlsDuringSetup.includes('class="confirm-atom" data-node-id="000000000001" disabled'));
assert(controlsDuringSetup.includes('セットアップ中…'));
assert(controlsDuringSetup.includes('data-unconfirmed="true" disabled'));
usbProvisioningInProgress = false;
assert(!usbSetupControls(candidate).includes('class="confirm-atom" data-node-id="000000000001" disabled'));
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)


def test_job_error_is_translated_without_echoing_untrusted_text():
    script = (ROOT/'app/static/admin.js').read_text()
    polling = script[script.index('const usbSetupStages'):script.index('async function submitUsbProvision')]
    harness = r'''const assert = require('node:assert/strict');
const statusLine = {};
let usbProvisioningInProgress = false;
let job = {stage: 'failed', node_id: '000000000001', error: 'factory_write_failed'};
const api = async () => job;
const loadNodes = async () => {};
const loadUsbNodes = async () => {};
''' + polling + r'''
(async () => {
  await pollUsbSetup();
  assert(statusLine.textContent.includes('初回'));
  assert(!statusLine.textContent.includes('factory_write_failed'));
  job.error = 'untrusted-private-psk-02:00:00:00:00:01';
  await pollUsbSetup();
  assert(!statusLine.textContent.includes(job.error));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)


def test_failed_submission_cannot_reuse_previous_completed_status():
    script = (ROOT/'app/static/admin.js').read_text()
    setup_js = script[script.index('const usbSetupStages'):script.index('// Restore progress')]
    harness = r'''const assert = require('node:assert/strict');
const statusLine = {};
let usbProvisioningInProgress = false;
const renderNodes = () => {};
const loadNodes = async () => {};
const loadUsbNodes = async () => {};
const api = async (path, options) => {
    if (options?.method === 'POST') throw Error('request failed');
    return {stage: 'completed', node_id: '000000000001', error: null};
};
''' + setup_js + r'''
(async () => {
  const button = {disabled: false, dataset: {device: '/dev/ttyACM0', nodeId: '000000000001', unconfirmed: 'false'}, closest: () => ({querySelector: () => null})};
  await submitUsbProvision(button);
  assert(!statusLine.textContent.includes('セットアップ完了'));
  assert(statusLine.className.includes('error'));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)


def test_setup_buttons_follow_job_state_and_prevent_repeat_posts():
    script = (ROOT/'app/static/admin.js').read_text()
    rendering = script[script.index('function nodeCard'):script.index('function saveNodeInputState')]
    setup_js = script[script.index('const usbSetupStages'):script.index('// Restore progress')]
    harness = r'''const assert = require('node:assert/strict');
let usbProvisioningInProgress = false, pendingNodeRegistration;
const text = value => String(value ?? '—');
const usbCandidatesByNodeId = new Map();
const statusLine = {};
const nodes = {innerHTML: '', querySelectorAll: () => [], querySelector: () => null};
const latestNodes = [];
const renderNodes = () => {};
global.setTimeout = () => 0;
let job = {stage: 'validating_firmware', node_id: '000000000001', error: null};
let posts = 0;
let acceptPost;
const api = async (path, options) => {
  if (options?.method === 'POST') { posts += 1; return new Promise(resolve => { acceptPost = resolve; }); }
  return job;
};
const loadNodes = async () => [];
const loadUsbNodes = async () => {};
''' + rendering + setup_js + r'''
const existing = {device: '/dev/ttyACM0', node_id: '000000000001', kind: 'omk_node', wifi_configured: true};
const newNode = {device: '/dev/ttyACM1', node_id: '000000000002', kind: 'unconfirmed_esp32s3', wifi_configured: false};
usbCandidatesByNodeId.set(existing.node_id, existing);
usbCandidatesByNodeId.set(newNode.node_id, newNode);
const existingButton = {disabled: false, textContent: '', dataset: {device: existing.device, nodeId: existing.node_id, unconfirmed: 'false'}, closest: () => ({querySelector: () => null})};
const newButton = {disabled: false, textContent: '', dataset: {device: newNode.device, nodeId: newNode.node_id, unconfirmed: 'true'}, closest: () => ({querySelector: () => ({checked: true})})};
(async () => {
  const firstSetup = submitUsbProvision(existingButton);
  assert.equal(posts, 1);
  assert.equal(existingButton.disabled, true);
  assert.equal(existingButton.textContent, 'セットアップ中…');
  assert(usbSetupControls(existing).includes('disabled') && usbSetupControls(existing).includes('セットアップ中…'));
  assert(usbSetupControls(newNode).includes('disabled') && usbSetupControls(newNode).includes('セットアップ中…'));
  assert(usbSetupControls(newNode).includes('class="confirm-atom" data-node-id="000000000002" disabled'));
  await submitUsbProvision(existingButton);
  await submitUsbProvision(newButton);
  assert.equal(posts, 1);
  acceptPost({accepted: true});
  await firstSetup;
  job = {stage: 'flashing_firmware', node_id: existing.node_id, error: null};
  await pollUsbSetup();
  assert.equal(usbProvisioningInProgress, true);
  assert(usbSetupControls(existing).includes('disabled'));
  job = {stage: 'completed', node_id: existing.node_id, error: null};
  await pollUsbSetup();
  assert.equal(usbProvisioningInProgress, false);
  assert(!usbSetupControls(existing).includes('disabled') && usbSetupControls(existing).includes('OMK Nodeをセットアップ'));
  job = {stage: 'failed', node_id: existing.node_id, error: 'firmware_write_failed'};
  await pollUsbSetup();
  assert.equal(usbProvisioningInProgress, false);
  assert(!usbSetupControls(newNode).includes('disabled'));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)


def test_running_job_recreates_disabled_button_after_page_reload():
    script = (ROOT/'app/static/admin.js').read_text()
    rendering = script[script.index('function nodeCard'):script.index('function saveNodeInputState')]
    polling = script[script.index('const usbSetupStages'):script.index('async function submitUsbProvision')]
    harness = r'''const assert = require('node:assert/strict');
let usbProvisioningInProgress = false, pendingNodeRegistration;
const text = value => String(value ?? '—');
const usbCandidatesByNodeId = new Map();
const statusLine = {};
const api = async () => ({stage: 'waiting_for_registration', node_id: '000000000001', error: null});
const loadNodes = async () => [];
const loadUsbNodes = async () => {};
const renderNodes = () => {};
global.setTimeout = () => 0;
''' + rendering + polling + r'''
(async () => {
  await pollUsbSetup();
  const candidate = {device: '/dev/ttyACM0', node_id: '000000000001', kind: 'unconfirmed_esp32s3'};
  assert.equal(usbProvisioningInProgress, true);
  assert(usbSetupControls(candidate).includes('disabled'));
  assert(usbSetupControls(candidate).includes('セットアップ中…'));
  assert(usbSetupControls(candidate).includes('confirm-atom'));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)


def test_node_refresh_shows_nodes_before_usb_confirmation_and_keeps_prior_state():
    script = (ROOT/'app/static/admin.js').read_text()
    rendering = script[script.index('function nodeCard'):script.index('function saveNodeInputState')]
    loading = script[script.index('let nodeRefreshPromise'):script.index('function stopRegistrationPoll')]
    harness = r'''
const assert = require('node:assert/strict');
let latestNodes = [], usbCandidatesByNodeId = new Map(), usbCandidatesInitialized = false, usbProvisioningInProgress = false, pendingNodeRegistration;
const text = value => String(value ?? '—');
const nodes = {innerHTML: ''}, statusLine = {};
let renders = [], pending = [], calls = 0;
const api = path => { calls++; return new Promise((resolve, reject) => pending.push({path, resolve, reject})); };
const renderNodes = () => { const visible = [...latestNodes, ...[...usbCandidatesByNodeId.values()].filter(candidate => !latestNodes.some(node => node.node_id === candidate.node_id))]; nodes.innerHTML = visible.map(nodeCard).join(''); renders.push(nodes.innerHTML); };
''' + rendering + loading + r'''
const oldNode = {node_id: '55f94c790e12', registration_state: 'provisioned', online: false, relay_active: false};
const onlineNode = {node_id: '3df94c3f187e', registration_state: 'provisioned', online: true, relay_active: false};
const blank = {node_id: oldNode.node_id, kind: 'unconfirmed_esp32s3', wifi_configured: false, device: '/dev/ttyACM0'};
const newCandidate = {node_id: '000000000001', kind: 'unconfirmed_esp32s3', wifi_configured: false, device: '/dev/ttyACM1'};
(async () => {
  const refresh = loadNodes();
  assert.equal(loadNodes(), refresh); // Initial load and polling share in-flight work.
  const [nodeRequest, usbRequest] = pending.splice(0);
  nodeRequest.resolve({nodes: [oldNode, onlineNode]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(renders.length, 1);
  assert(nodes.innerHTML.includes('Wi-Fi: 確認中'));
  assert(nodes.innerHTML.includes('Node ID: 3df94c3f187e'));
  assert(nodes.innerHTML.includes('Wi-Fi: Wi-Fi設定済み'));
  usbRequest.resolve({nodes: [blank, newCandidate]});
  await refresh;
  assert.equal(renders.length, 2);
  assert(nodes.innerHTML.includes('Wi-Fi: 未設定'));
  assert(nodes.innerHTML.includes('対応機能: セットアップ後に確認'));
  assert(nodes.innerHTML.includes('Node ID: 000000000001'));
  assert(!nodes.innerHTML.includes('BLE relay: 稼働中'));
  assert.equal(calls, 2);

  // Polling preserves the already-rendered list while its next USB scan runs.
  const pollRefresh = loadNodes();
  const [pollNodeRequest, pollUsbRequest] = pending.splice(0);
  pollNodeRequest.resolve({nodes: [oldNode, onlineNode]});
  await new Promise(resolve => setImmediate(resolve));
  assert(nodes.innerHTML.includes('Node ID: 000000000001'));
  pollUsbRequest.resolve({nodes: [blank]});
  await pollRefresh;
  assert(!nodes.innerHTML.includes('Node ID: 000000000001'));
  usbProvisioningInProgress = true;
  let setupRefresh = loadNodes();
  assert.equal(pending.length, 1);
  pending.shift().resolve({nodes: [oldNode]});
  await setupRefresh;
  assert(nodes.innerHTML.includes('disabled'));
  assert(usbCandidatesByNodeId.has(oldNode.node_id));
  usbProvisioningInProgress = false;
  setupRefresh = loadNodes();
  pending.shift().resolve({nodes: [oldNode]});
  pending.shift().reject(Error('USB unavailable'));
  await setupRefresh;
  assert.equal(statusLine.textContent, 'USB unavailable');
  assert(usbCandidatesByNodeId.has(oldNode.node_id));
  setupRefresh = loadNodes();
  pending.shift().reject(Error('Node unavailable'));
  pending.shift().resolve({nodes: []});
  await setupRefresh;
  assert(nodes.innerHTML.includes('Node unavailable'));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True, timeout=10)
    assert 'Promise.all([loadNodes(), loadUsbNodes()])' not in script
    assert 'loadRegistered(); loadNodes(); window.setInterval' in script
