from pathlib import Path
import json


SOURCE = Path(__file__).parents[1] / "src"


def test_gateway_credentials_are_a_readback_verified_omk_nvs_record():
    source = (SOURCE / "gateway_credentials.c").read_text()

    assert 'OMK_GATEWAY_CREDENTIALS_NAMESPACE "omk_net"' in source
    assert 'OMK_GATEWAY_CREDENTIALS_KEY "gw_cred"' in source
    assert "nvs_set_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY" in source
    assert "nvs_commit(nvs)" in source
    assert "nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY" in source
    assert "memcmp(&expected, &verified" in source


def test_clear_gateway_credentials_erases_only_the_dedicated_key_and_verifies_absence():
    credentials = (SOURCE / "gateway_credentials.c").read_text()
    wifi = (SOURCE / "wifi_station.c").read_text()
    usb = (SOURCE / "usb_provisioning.c").read_text()
    clear = credentials[credentials.index("esp_err_t gateway_credentials_clear") : credentials.index("esp_err_t gateway_credentials_migrate_legacy")]
    assert "nvs_erase_key(nvs, OMK_GATEWAY_CREDENTIALS_KEY)" in clear
    assert "if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;" in clear
    assert "nvs_commit(nvs)" in clear
    assert "nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY" in clear
    assert "ESP_ERR_NVS_NOT_FOUND" in clear
    assert "gateway_credentials_clear" in wifi
    assert 'strcmp(command->valuestring, "clear_wifi")' in usb
    assert "wifi_station_clear_saved_credentials" in usb
    assert usb.index("wifi_station_clear_saved_credentials") < usb.index("schedule_reboot()")
    for forbidden in ("NODE_NVS_NAMESPACE", "prov_pop", "factory_secret", "boot_count", "logical_id"):
        assert forbidden not in clear


def test_usb_identify_exposes_only_wifi_configured_boolean_and_reboots_in_its_own_task():
    usb = (SOURCE / "usb_provisioning.c").read_text()
    wifi = (SOURCE / "wifi_station.c").read_text()
    assert '\\"wifi_configured\\":%s' in usb
    assert "wifi_station_has_saved_credentials" in usb
    assert "xTaskCreate(reboot_task" not in usb
    assert "if (reboot_scheduled)" in usb and "esp_restart();" in usb
    assert "gateway_credentials_load(&credentials)" in wifi




def test_legacy_mesh_parent_state_is_not_migrated_as_gateway_credential():
    source = (SOURCE / "gateway_credentials.c").read_text()

    migration = source[source.index("esp_err_t gateway_credentials_migrate_legacy") :]
    assert "legacy->sta.bssid_set" in migration
    assert "is_mesh_default_ap_ssid(legacy->sta.ssid, ssid_length)" in migration
    assert "return ESP_ERR_NOT_SUPPORTED;" in migration
