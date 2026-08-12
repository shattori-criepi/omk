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

The current Pi-captured manufacturer layouts decode SwitchBot Meter temperature
and humidity, and Meter Pro CO2's CO2 value only. Meter Pro CO2 temperature and
humidity deliberately remain raw until their positions are confirmed.

## Raspberry Pi validation notes

With the Pi's current BlueZ/bleak combination, SwitchBot advertisements can be
reported exclusively as `manufacturer_data[0x0969]`; an empty `service_data`
field is therefore not an error. `raw_scan` prints both structures for a new
fixture:

```sh
PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30
```

Before starting the service, inspect the adapter with `rfkill list bluetooth`
and `bluetoothctl show`. If it is soft blocked, run `sudo rfkill unblock
bluetooth`, then `sudo bluetoothctl power on`. The setup script performs these
steps opportunistically and logs a warning rather than failing installation if
the adapter is missing or cannot be powered on.

Verified on a Raspberry Pi:

- SwitchBot 温湿度計: advertisement reception, temperature, and humidity decode.
- SwitchBot CO2センサー: advertisement reception and CO2 ppm decode. Temperature
  and humidity decode are **not yet verified**.

For a line-oriented raw capture on a Pi, use
`PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30`.
