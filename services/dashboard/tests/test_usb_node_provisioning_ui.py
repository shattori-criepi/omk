from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sensor_management_integrates_usb_provisioning_into_matching_node_card_without_secrets():
    template = (ROOT / "app/templates/admin_sensors.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/admin.js").read_text(encoding="utf-8")
    source = template + script
    assert '?v=20260902-usb-node-provisioning-retry' in template
    assert 'id="usb-nodes"' not in template
    for text in ("usbCandidatesByNodeId", "const usbCandidate = usbCandidatesByNodeId.get(node.node_id)", "このNodeを設定", "設定中…", "/setup/usb-nodes", "/setup/usb-provision", "Promise.all([loadNodes(), loadUsbNodes()])"):
        assert text in source
    assert "usbProvisioningInProgress" in script
    assert "if (usbProvisioningInProgress) return;" in script
    assert 'node.registration_state === "registered" ?' in script
    assert 'node.registration_state === "provisioned" ?' in script
    assert "OMK_SYSTEM_MANAGER_TOKEN" not in source and "Bearer " not in source
