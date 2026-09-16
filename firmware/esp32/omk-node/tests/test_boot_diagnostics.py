import json
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_reset_reason_mapping_covers_required_idf_reasons():
    source = (SOURCE / "boot_diagnostics.c").read_text()
    expected = {
        "ESP_RST_POWERON": "power_on",
        "ESP_RST_EXT": "external",
        "ESP_RST_SW": "software",
        "ESP_RST_PANIC": "panic",
        "ESP_RST_TASK_WDT": "task_watchdog",
        "ESP_RST_INT_WDT": "interrupt_watchdog",
        "ESP_RST_WDT": "watchdog",
        "ESP_RST_BROWNOUT": "brownout",
        "ESP_RST_DEEPSLEEP": "deep_sleep",
        "ESP_RST_SDIO": "sdio",
        "ESP_RST_USB": "usb",
        "ESP_RST_JTAG": "jtag",
        "ESP_RST_EFUSE": "efuse",
        "ESP_RST_PWR_GLITCH": "power_glitch",
        "ESP_RST_CPU_LOCKUP": "cpu_lockup",
    }
    for enum_name, status_name in expected.items():
        assert f'case {enum_name}: return "{status_name}";' in source
    assert 'default: return "unknown";' in source


def test_boot_counter_is_best_effort_nvs_and_persisted():
    source = (SOURCE / "boot_diagnostics.c").read_text()
    assert 'nvs_open(OMK_BOOT_DIAGNOSTICS_NAMESPACE, NVS_READWRITE, &nvs)' in source
    assert 'nvs_get_u32(nvs, OMK_BOOT_COUNT_KEY, &previous_count)' in source
    assert 'nvs_set_u32(nvs, OMK_BOOT_COUNT_KEY, boot_count)' in source
    assert "nvs_commit(nvs)" in source
    assert "Boot counter NVS write failed" in source


def test_node_status_has_boot_and_heap_diagnostics_with_capacity():
    source = (SOURCE / "mesh_network.c").read_text()
    assert "#define OMK_MESH_STATUS_PAYLOAD_SIZE 1152" in source
    for field in (
        "minimum_free_heap_bytes",
        "reset_reason",
        "reset_reason_code",
        "boot_count",
    ):
        assert f'\\"{field}\\"' in source
    assert "esp_get_minimum_free_heap_size()" in source

    # Worst-case field values leave status payload capacity; this mirrors the
    # status formatter without changing unrelated registration payloads.
    payload = {
        "node_id": "f" * 12, "mesh_layer": -2147483648, "is_root": False,
        "parent_bssid": "ff:ff:ff:ff:ff:ff", "rssi_dbm": -2147483648,
        "rssi_valid": False, "ip": "255.255.255.255",
        "parent_change_count": 4294967295, "parent_disconnect_count": 4294967295,
        "is_rootless": False, "rootless_duration_s": 4294967295,
        "last_parent_disconnect_reason": 4294967295,
        "last_wifi_disconnect_reason": 4294967295, "root_switch_count": 4294967295,
        "mqtt_disconnect_count": 4294967295, "uptime_s": 4294967295,
        "free_heap_bytes": 4294967295, "minimum_free_heap_bytes": 4294967295,
        "reset_reason": "interrupt_watchdog", "reset_reason_code": 4294967295,
        "boot_count": 4294967295,
    }
    assert len(json.dumps(payload, separators=(",", ":"))) < 1152


def test_registration_and_mesh_status_have_separate_payload_capacity():
    source = (SOURCE / "mqtt_registration.c").read_text()
    assert "#define OMK_REGISTRATION_PAYLOAD_SIZE 256" in source
    assert "#define OMK_MQTT_MESH_STATUS_PAYLOAD_SIZE 1152" in source


def test_switchbot_scan_is_active_and_joins_advertisement_and_scan_response_fragments():
    discovery = (SOURCE / "discovery_ble.c").read_text()
    relay = (SOURCE / "switchbot_relay.c").read_text()
    assert ".scan_type = BLE_SCAN_TYPE_ACTIVE" in discovery
    assert "Active SwitchBot scan" in discovery
    assert "never connects to or" in discovery and "pairs with the observed devices" in discovery
    assert "adv_data_len + param->scan_rst.scan_rsp_len" in discovery
    assert "SWITCHBOT_FRAGMENT_JOIN_WINDOW_US" in relay
    assert "Active scan can report ADV and SCAN_RSP separately" in relay
    assert "slot->manufacturer_data" in relay and "slot->service_data" in relay
