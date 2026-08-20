from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src"


def test_gateway_credentials_are_a_readback_verified_omk_nvs_record():
    source = (SOURCE / "gateway_credentials.c").read_text()

    assert 'OMK_GATEWAY_CREDENTIALS_NAMESPACE "omk_net"' in source
    assert 'OMK_GATEWAY_CREDENTIALS_KEY "gw_cred"' in source
    assert "nvs_set_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY" in source
    assert "nvs_commit(nvs)" in source
    assert "nvs_get_blob(nvs, OMK_GATEWAY_CREDENTIALS_KEY" in source
    assert "memcmp(&expected, &verified" in source


def test_runtime_sta_configuration_is_not_the_gateway_credential_store():
    wifi = (SOURCE / "wifi_station.c").read_text()
    network = (SOURCE / "mesh_network.c").read_text()

    save = wifi[wifi.index("esp_err_t wifi_station_save_credentials") : wifi.index("esp_err_t wifi_station_set_saved_credentials_for_development")]
    assert "gateway_credentials_save" in save
    assert "esp_wifi_set_config" not in save
    start = network[network.index("esp_err_t mesh_network_start_prepared") :]
    assert "gateway_credentials_load(&gateway)" in start
    assert "esp_wifi_get_config(WIFI_IF_STA" not in start
    assert "mesh_credentials_derive(gateway.ssid" in start
    assert "config.router.ssid, gateway.ssid" in start


def test_legacy_mesh_parent_state_is_not_migrated_as_gateway_credential():
    source = (SOURCE / "gateway_credentials.c").read_text()

    migration = source[source.index("esp_err_t gateway_credentials_migrate_legacy") :]
    assert "legacy->sta.bssid_set" in migration
    assert "is_mesh_default_ap_ssid(legacy->sta.ssid, ssid_length)" in migration
    assert "return ESP_ERR_NOT_SUPPORTED;" in migration


def test_sen66_mqtt_failure_does_not_stop_periodic_measurement():
    sensor_manager = (SOURCE / "sensor_manager.c").read_text()

    publish = sensor_manager[sensor_manager.index("err = mqtt_registration_publish_sen66") :]
    assert "mqtt_warning_logged = true;" in publish
    assert "mqtt_warning_logged = false;" in publish
    assert "vTaskDelay(pdMS_TO_TICKS(SEN66_MEASUREMENT_INTERVAL_MS));" in publish
