"""One-off raw BLE capture for validating a new decoder on the Pi.

Run: PYTHONPATH=src python -m omk_ble.raw_scan --seconds 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime


async def main(seconds: int) -> None:
    from bleak import BleakScanner

    def found(device, advertisement) -> None:
        print(json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "address": device.address, "rssi": advertisement.rssi,
            "service_uuids": advertisement.service_uuids,
            "manufacturer_data": {f"{key:04x}": value.hex() for key, value in advertisement.manufacturer_data.items()},
            "service_data": {key: value.hex() for key, value in advertisement.service_data.items()},
        }, ensure_ascii=False))

    scanner = BleakScanner(detection_callback=found)
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(main(args.seconds))
