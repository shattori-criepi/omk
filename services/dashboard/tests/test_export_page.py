import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def test_export_page_contract():
    template=(ROOT/'app/templates/admin_export.html').read_text()
    script=(ROOT/'app/static/export.js').read_text()
    menu=(ROOT/'app/templates/admin.html').read_text()
    assert '/api/export/usb/status' in script and '/api/export/usb' in script
    assert re.search(r'timeZone\s*:\s*["\']Asia/Tokyo["\']', script) and 'unmount_failed' in script
    assert 'export-button' in template and 'データ書き出し' in menu
    assert 'OMK_SYSTEM_MANAGER_TOKEN' not in template+script
