from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src"


def test_registered_logical_device_status_replaces_retained_offline_on_connect():
    mqtt_registration = (SOURCE / "mqtt_registration.c").read_text()

    assert '"omk/%s/status"' in mqtt_registration
    assert '\\"status\\":\\"offline\\"' in mqtt_registration
    assert "load_persisted_device_status();" in mqtt_registration
    assert ".session.last_will" in mqtt_registration
    assert ".retain = device_status_configured ? 1 : 0" in mqtt_registration

    connected = mqtt_registration[
        mqtt_registration.index("case MQTT_EVENT_CONNECTED:") : mqtt_registration.index("case MQTT_EVENT_DATA:")
    ]
    assert 'publish_device_status("online");' in connected

    registration = mqtt_registration[
        mqtt_registration.index("static void process_registration_config") : mqtt_registration.index("static void mqtt_event_handler")
    ]
    assert "configure_device_status(logical_id->valuestring)" in registration
    assert 'publish_device_status("online");' in registration
