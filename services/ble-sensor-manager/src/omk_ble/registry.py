"""Human-readable, atomically-written BLE sensor registration store."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .models import RegisteredSensor

SENSOR_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
LEGACY_MODEL_NAMES = {
    "meter": "temperature_humidity_sensor",
    "meter_plus": "temperature_humidity_sensor",
    "meter_pro_co2": "co2_sensor",
}


class RegistryError(ValueError):
    pass


class SensorRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self) -> list[RegisteredSensor]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            sensors = data.get("sensors", [])
            if not isinstance(sensors, list):
                raise RegistryError("sensors must be an array")
            return [self._normalise_sensor(RegisteredSensor.from_dict(item)) for item in sensors]
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise RegistryError(f"cannot read sensor registry: {error}") from error

    def register(self, sensor: RegisteredSensor) -> RegisteredSensor:
        sensor = self._normalise_sensor(sensor)
        if not SENSOR_ID_RE.fullmatch(sensor.sensor_id):
            raise RegistryError("sensor_id must use lowercase letters, digits, and hyphens")
        sensors = self.list()
        if any(item.device_key == sensor.device_key for item in sensors):
            raise RegistryError("this physical BLE device is already registered")
        if any(item.sensor_id == sensor.sensor_id for item in sensors):
            raise RegistryError("sensor_id is already in use")
        self._write(sensors + [sensor])
        return sensor

    @staticmethod
    def _normalise_sensor(sensor: RegisteredSensor) -> RegisteredSensor:
        """Read legacy SwitchBot model names without retaining them on writes."""
        model = LEGACY_MODEL_NAMES.get(sensor.model, sensor.model)
        if model == sensor.model:
            return sensor
        return RegisteredSensor(
            device_key=sensor.device_key, sensor_id=sensor.sensor_id,
            sensor_type=sensor.sensor_type, vendor=sensor.vendor, model=model,
            location=sensor.location, display_name=sensor.display_name, enabled=sensor.enabled,
        )

    def update(self, device_key: str, *, sensor_id: str, display_name: str, location: str, enabled: bool) -> RegisteredSensor:
        """Update editable logical settings while preserving physical identity."""
        if not SENSOR_ID_RE.fullmatch(sensor_id):
            raise RegistryError("sensor_id must use lowercase letters, digits, and hyphens")
        sensors = self.list()
        existing = next((sensor for sensor in sensors if sensor.device_key == device_key), None)
        if existing is None:
            raise KeyError(device_key)
        if any(sensor.sensor_id == sensor_id and sensor.device_key != device_key for sensor in sensors):
            raise RegistryError("sensor_id is already in use")
        updated = RegisteredSensor(
            device_key=existing.device_key, sensor_id=sensor_id,
            sensor_type=existing.sensor_type, vendor=existing.vendor, model=existing.model,
            location=location, display_name=display_name, enabled=enabled,
        )
        self._write([updated if sensor.device_key == device_key else sensor for sensor in sensors])
        return updated

    def _write(self, sensors: list[RegisteredSensor]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"sensors": [sensor.as_dict() for sensor in sensors]}, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
            temporary_path = output.name
        os.replace(temporary_path, self.path)
        os.chmod(self.path, 0o640)
