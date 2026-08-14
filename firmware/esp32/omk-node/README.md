# OMK ESP32 Node — discovery-only provisioning foundation

This common PlatformIO project builds the same discovery firmware for an
AtomS3 Lite and original M5StickC. It intentionally contains no board display,
LED, or button behaviour.

## BLE advertisement v1

- Service UUID: `7d2a4d90-7b64-4e3a-9f37-95e77d7b5101`
- Service data, exactly 10 bytes: `version(1) | state(1) | capabilities(2, big-endian) | node_id(6)`
- Version: `1`
- State: `0` = unregistered. Only this discovery state is implemented.
- `node_id`: a stable 48-bit FNV-1a-derived value from the ESP factory eFuse
  MAC, encoded as a lowercase 12-hex-character internal ID. The factory BLE
  MAC itself is neither advertised as a field nor shown by the Dashboard.
- Capability bits: `0x0001=ble_scan`, `0x0002=sen66`; flags are combinable.

The advertisement contains no Wi-Fi SSID/password, MQTT credentials,
`site_uuid`, SORACOM data, or other secrets. Gateway detection is passive only;
this stage has no GATT credential exchange and no registration action.

## Build

```bash
cd firmware/esp32/omk-node
pio run -e atom-s3-lite
pio run -e m5stick-c
```

`m5stick-c` targets the original ESP32 M5StickC. M5StickC Plus/Plus2 variants
have not been selected deliberately; add a separately verified board profile
before building for either.
