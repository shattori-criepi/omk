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

`device_key` is the physical identity (`switchbot:<lowercase MAC without
colons>`); `sensor_id` is the OMK logical identity. The registry stores only
`device_key`, `sensor_id`, `sensor_type`, vendor, model, location,
`display_name`, and `enabled`. Latest measurements, RSSI, receive time, and raw
advertisements remain runtime state and are never written into the registry.

Dashboard's sensor-management setup flow discovers unregistered advertisements,
classifies them, proposes an ID, then registers a chosen display name and
location. It proposes the first unused `th-xxx` for
`temperature_humidity_sensor` and `waterproof_sensor`, `co2-xxx` for
`co2_sensor`, and corresponding `motion-xxx` / `contact-xxx` IDs for state
sensors. Replacement hardware normally receives a new logical ID.

Unknown SwitchBot advertisements intentionally appear with raw manufacturer and
service data hex during setup. This permits real-device validation for Meter Pro
CO2, motion, and contact sensors without publishing guessed values.

OMK keeps product names separate from internal models: SwitchBot Meter and
Meter Plus use `temperature_humidity_sensor`, and SwitchBot Meter Pro CO2 uses
`co2_sensor`. Existing registry entries with the former `meter`, `meter_plus`,
or `meter_pro_co2` values are read compatibly and rewritten with the current
names on the next registry update.

| OMK model | Dashboard name | sensor type | Normalized values |
| --- | --- | --- | --- |
| `temperature_humidity_sensor` | SwitchBot 温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `co2_sensor` | SwitchBot CO2センサー | `environment` | temperature, humidity, `co2_ppm` |
| `motion_sensor` | SwitchBot 人感センサー | `motion` | `motion_state` (0=不在, 1=検知) |
| `contact_sensor` | SwitchBot 開閉センサー | `contact` | `contact_state` (0=閉, 1=開) |
| `waterproof_sensor` | SwitchBot 防水温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `plug_sensor` | SwitchBot プラグミニ | `power` | `power_w`, `switch_state` (0=OFF, 1=ON) |

The waterproof model is not limited to outdoors; deployment use belongs in
`location` (for example, 屋外 or 浴室). `humidity_percent`, `outdoor_meter`, and
`waterproof_meter` are not OMK schema/model names.

Advertisements are received continuously without pairing. For enabled sensors,
environment data is published immediately on first valid reception and then no
more often than every 10 seconds per `device_key`; runtime state still updates
on every advertisement. Motion and contact publish state changes immediately
and also publish their current state no more often than every 10 seconds. These
limits use a monotonic clock. Plug Mini power follows the same 10-second limit,
but a `switch_state` transition publishes immediately. Disabled sensors still
update runtime state but do not publish MQTT. Plug Mini proposes `plug-xxx` IDs
and publishes `omk/<sensor_id>/power` with `power_w` and `switch_state`.

The current Pi-captured manufacturer layouts decode SwitchBot Meter temperature
and humidity, and Meter Pro CO2 temperature, humidity, and CO2. The CO2 layout
is based on matching measurements from two physical devices; it does not use a
per-device MAC address or assume that its variable bytes are constants.

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
- SwitchBot CO2センサー: advertisement reception, temperature, humidity, and CO2
  ppm decode (verified against two physical devices).
- SwitchBot 人感センサー: advertisement reception, state transition, and periodic
  MQTT publish.
- SwitchBot 開閉センサー: advertisement reception, state transition, and periodic
  MQTT publish.
- SwitchBot 防水温湿度計: advertisement reception and dedicated
  temperature/humidity manufacturer-layout decode.
- SwitchBot プラグミニ: advertisement-only power and switch-state decode.
  Its manufacturer layout is the primary identifier; the public `0x67` and
  observed domestic `0x6a` service-data values are supplementary only. Power
  masks the overload flag from the MSB before applying the 0.1 W scale.

### SwitchBot プラグミニ実機確認

Raspberry Piと実Plug Miniで、ペアリングなしのBLE advertisement受信からDashboardの
BLEセットアップ、`plug-001`登録、MQTT、JSONL保存、Harvest 1分集約、SORACOM
Harvest Data送信までを確認した。内部modelは`plug_sensor`、sensor typeは`power`、
Dashboard表示名は「SwitchBot プラグミニ」である。

manufacturer dataではstate byteのbit 7を`switch_state`として用い、0=OFF、1=ONを
実機のOFF→ON操作と照合した。電力は公式仕様に従い
`raw_power = ((byte10 & 0x7f) << 8) | byte11`、`power_w = raw_power / 10.0`
でdecodeする。byte10のbit 7はoverload flagであり電力値から除外する。低負荷で
`0x0034`=5.2 W、`0x0035`=5.3 W（アプリ表示約5.1〜5.2 W）、高負荷で
`0x05f2`=152.2 W、`0x06c4`=173.2 W、`0x06cd`=174.1 W（実負荷約170 W）を確認し、
低負荷・高負荷とも0.1 W scaleが成立した。

国内実機ではfd3d Service Dataとして`6a0064`を観測したが、これは公式device typeと
は記載しない。Service Dataは補助識別に留め、Plug Mini判定とmeasurement decodeは
manufacturer dataの12-byte layoutを主根拠にする。

For a line-oriented raw capture on a Pi, use
`PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30`.
