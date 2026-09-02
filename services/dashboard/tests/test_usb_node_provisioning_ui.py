from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sensor_management_separates_usb_nodes_and_never_exposes_management_token():
    template = (ROOT / "app/templates/admin_sensors.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/admin.js").read_text(encoding="utf-8")
    source = template + script
    assert 'id="usb-nodes"' in template
    for text in ("USB接続された未設定Node", "このNodeを設定", "設定中…", "/setup/usb-nodes", "/setup/usb-provision"):
        assert text in source
    assert "OMK_SYSTEM_MANAGER_TOKEN" not in source and "Bearer " not in source
