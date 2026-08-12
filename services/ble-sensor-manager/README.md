# OMK BLE sensor manager

This Raspberry Pi host service passively receives BlueZ advertisements, maps a
physical `device_key` to an OMK logical `sensor_id`, and publishes vendor-neutral
MQTT topics such as `omk/living-env-01/environment`.

It deliberately runs outside the Dashboard container; Docker reaches port 8787
through its host gateway. It needs no BlueZ DBus socket or privileged container,
so Bluetooth access remains isolated to the host service. Limit port 8787 to the
Docker bridge in the Pi firewall if the Dashboard is exposed beyond the local UI.

Run `scripts/setup-ble-sensor-manager.sh` on the Pi to create its virtualenv and
install the systemd unit. Registration is readable JSON at
`data/ble/sensors.json`.

Unknown SwitchBot advertisements intentionally appear with raw manufacturer and
service data hex during setup. This permits real-device validation for Meter Pro
CO2, motion, and contact sensors without publishing guessed values.

For a line-oriented raw capture on a Pi, use
`PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30`.
