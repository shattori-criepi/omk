from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_usb_export_ui_static_contract() -> None:
    template = (ROOT / "app/templates/admin_export.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/export.js").read_text(encoding="utf-8")
    menu = (ROOT / "app/templates/admin.html").read_text(encoding="utf-8")
    assert 'href="/admin/export"' in menu and "データ書き出し" in menu
    assert '<html lang="ja" class="admin-document">' in template
    for element in (
        "id=\"from-display\"",
        "id=\"to-display\"",
        "id=\"datasets\"",
        "id=\"export-button\"",
        "id=\"export-status-panel\"",
        "id=\"export-status-title\"",
        "id=\"usb-status\"",
        "id=\"export-status-detail\"",
    ):
        assert element in template
    assert 'type="date"' not in template and 'type="text"' not in template
    assert 'id="calendar-dialog"' in template and 'id="calendar-prev"' in template and 'id="calendar-next"' in template
    assert "OMK_SYSTEM_MANAGER_TOKEN" not in template + script and "Bearer " not in template + script
    assert 'timeZone: "Asia/Tokyo"' in script and "calendar-grid" in script
    assert "new Date().toISOString().slice(0, 10)" not in script
    assert "function renderUsbStatus" in script and "function renderExportState" in script
    assert 'id="export-message"' not in template and 'id="export-job-status"' not in template
    assert "データを書き出しています…" in script and "書き出し中…" in script
    assert "書き出しが完了しました" in script and "USBメモリを取り外せます" in script
    assert "USBメモリを安全に取り外せません" in script and "まだ取り外さないでください" in script
    assert "function isUnmountBlocked" in script
    assert 'usb?.mount_state === "mounted"' in script
    assert 'exportState?.error_code === "unmount_failed"' in script
    assert 'lastExport = { state: "running" }' in script
    assert "expected_identity: lastUsb.identity" in script
    assert "USBメモリが変更されました。接続を確認してもう一度実行してください" in script
    assert 'response.status === 409 && detail?.detail === "usb_changed"' in script
    assert 'error?.message === "usb_changed" ? "usb_changed" : "export_failed"' in script
    assert "await refresh();" in script and "window.setInterval(refresh, 3000)" in script
    assert "過去7日" in template and "過去30日" in template and "今月" in template
    assert "export-datasets" in template
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in (ROOT / "app/static/display.css").read_text(encoding="utf-8")
    css = (ROOT / "app/static/display.css").read_text(encoding="utf-8")
    assert "overflow-y: auto;" in css and "max-height: 202px;" in css
    assert ".export-management-page .export-datasets::-webkit-scrollbar" in css
    assert "min-height: 58px;" in css and ".export-status--running" in css
    assert "min-height: 82px;" in css
    assert ".export-management-page {\n  height: auto;" in css
    assert "overflow: visible;" in css and "grid-auto-rows: max-content;" in css
    export_scroll_css = css[css.index("/* The export page is a scrollable admin document"):]
    assert "\n  height: 100dvh;" not in export_scroll_css
    assert "<details class=\"export-help\">" in template
    assert "/static/display.css?v=20260902-export-touch-6" in template
    assert "/static/export.js?v=20260911-usb-identity-1" in template
