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
''' + rendering + submit + r'''
const candidate = {device: '/dev/ttyACM0', node_id: '000000000001', kind: 'omk_node', wifi_configured: true};
usbCandidatesByNodeId.set(candidate.node_id, candidate);
let card = nodeCard({node_id: candidate.node_id, registration_state: 'registered', logical_id: 'sen66-001'});
assert(card.includes('OMK Nodeをセットアップ'));
assert(card.includes('毎回'));
assert(card.includes('sen66-001'));
candidate.kind = 'unconfirmed_esp32s3';
card = nodeCard(candidate);
assert(card.includes('ESP32-S3を検出') && card.includes('class="confirm-atom"'));
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
