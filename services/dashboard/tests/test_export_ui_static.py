from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_usb_export_ui_static_contract() -> None:
    template = (ROOT / "app/templates/admin_export.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/export.js").read_text(encoding="utf-8")
    menu = (ROOT / "app/templates/admin.html").read_text(encoding="utf-8")
    assert 'href="/admin/export"' in menu and "データ書き出し" in menu
    for element in ("id=\"from\"", "id=\"to\"", "id=\"datasets\"", "id=\"export-button\""):
        assert element in template
    assert "OMK_SYSTEM_MANAGER_TOKEN" not in template + script and "Bearer " not in template + script
    assert "timeZone:'Asia/Tokyo'" in script
    assert "new Date().toISOString().slice(0,10)" not in script
    assert "e.state==='running'" in script and "button.disabled=e.state==='running'||!ok" in script
    assert "e.state==='succeeded'" in script and "e.state==='failed'" in script
    assert "unmount_failed" in script and "USBメモリを取り外せます" in script
    assert "setInterval(refresh,3000)" in script
