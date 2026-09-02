from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src"


def test_sen66_liveness_timeout_reuses_existing_recovery_path():
    source = (SOURCE / "sensor_manager.c").read_text()

    assert "#define SEN66_MEASUREMENT_LIVENESS_TIMEOUT_MS 60000" in source
    assert "SEN66 measurement timeout: no successful measurement for" in source
    assert 'recover_sen66(&sen66, "measurement liveness timeout")' in source
    assert "sen66_sensor_stop(sen66)" in source
    assert "vTaskDelay(pdMS_TO_TICKS(SEN66_RETRY_INTERVAL_MS));" in source


def test_sen66_liveness_resets_only_after_a_successful_measurement_read():
    source = (SOURCE / "sensor_manager.c").read_text()

    ready = source[source.index("} else if (data_ready) {") : source.index("uint64_t liveness_reference_ms")]
    assert "last_successful_measurement_ms = monotonic_milliseconds();" in ready
    assert "mqtt_registration_publish_sen66" in ready
    assert ready.index("last_successful_measurement_ms") < ready.index("mqtt_registration_publish_sen66")


def test_sen66_read_failures_and_liveness_timeouts_have_separate_diagnostics():
    source = (SOURCE / "sensor_manager.c").read_text()
    mqtt = (SOURCE / "mqtt_registration.c").read_text()

    assert "++sen66_diagnostics.measurement_timeout_count;" in source
    assert 'recover_sen66(&sen66, "consecutive read failures")' in source
    assert "if (consecutive_read_failures >= 3)" in source
    for field in ("sen66_rc", "sen66_to"):
        assert field in mqtt
