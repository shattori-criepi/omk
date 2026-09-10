from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src"


def test_sen66_liveness_timeout_reuses_existing_recovery_path():
    source = (SOURCE / "sensor_manager.c").read_text()

    assert "#define SENSOR_MEASUREMENT_LIVENESS_TIMEOUT_MS 60000" in source
    assert 'recover_sensor(slot, "measurement liveness timeout")' in source
    assert "slot->driver->close(slot->context)" in source
    assert "slot->next_attempt_ms = monotonic_milliseconds() + SENSOR_RETRY_INTERVAL_MS;" in source


def test_sen66_liveness_resets_only_after_a_successful_measurement_read():
    source = (SOURCE / "sensor_manager.c").read_text()

    ready = source[source.index("} else if (data_ready) {") : source.index("if (monotonic_milliseconds() - slot->last_successful_measurement_ms")]
    assert "last_successful_measurement_ms = monotonic_milliseconds();" in ready
    assert "slot->driver->publish" in ready
    assert ready.index("last_successful_measurement_ms") < ready.index("slot->driver->publish")


def test_sen66_read_failures_and_liveness_timeouts_have_separate_diagnostics():
    source = (SOURCE / "sensor_manager.c").read_text()
    mqtt = (SOURCE / "mqtt_registration.c").read_text()

    assert "++slot->measurement_timeout_count;" in source
    assert 'recover_sensor(slot, "identity lost or consecutive read failures")' in source
    assert "slot->consecutive_read_failures >= 3" in source
    for field in ("sen66_rc", "sen66_to"):
        assert field in mqtt
